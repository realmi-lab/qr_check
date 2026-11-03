from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
import threading

import pytest

from roombot import admin, bookings, checkin, clock, commands, db, rooms, room_channels, scheduler
from roombot.config import settings


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    original_path = db._path
    clock.freeze(datetime(2026, 11, 4, 8, tzinfo=clock.TZ))
    db.use_database(tmp_path / 'test.sqlite3')
    monkeypatch.setattr(settings, 'room_channels', False)
    monkeypatch.setattr(settings, 'morning_brief_at', '')
    monkeypatch.setattr(settings, 'demo_mode', False)
    with db.tx(write=True) as conn:
        conn.executemany('INSERT INTO rooms(id,code,name,floor,capacity) VALUES(?,?,?,?,?)',
                         [(1,'9A','9A',9,6),(2,'BTC','Bitcoin',22,10)])
        conn.executemany('INSERT INTO employees(id,emp_no,name,email,department,position) VALUES(?,?,?,?,?,?)',
                         [(1,'E1','예약자','a@example.test','개발','팀원'),
                          (2,'E2','참석자','b@example.test','개발','팀원'),
                          (3,'E3','외부인','c@example.test','운영','팀원')])
    checkin.noshow_enforced_from()
    yield
    clock.freeze(None)
    db._path = original_path
    rooms.clear_cache()


def at(hour=9, minute=0):
    return datetime(2026,11,4,hour,minute,tzinfo=clock.TZ)


def reservation(room=1, start=None, end=None, attendees=None):
    return bookings.reserve(room, 1, start or at(), end or at(10), '회의', attendees or [2])


def test_persistence_backup_and_utc_normalization(tmp_path):
    rid = reservation()
    assert db.database_path().stat().st_mode & 0o777 == 0o600
    with db.tx() as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()['journal_mode'] == 'wal'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    path = db.backup(tmp_path / 'backup.sqlite3')
    with sqlite3.connect(path) as raw:
        assert raw.execute('SELECT start_at FROM reservations WHERE id=?',(rid,)).fetchone()[0] == '2026-11-04T00:00:00.000000+00:00'
        assert raw.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    with pytest.raises(FileExistsError):
        db.backup(path)
    db.use_database(path)
    assert rooms.room_schedule(1, at().date())[0]['start_at'] == at()


def test_empty_store_has_no_automatic_sample_data(tmp_path):
    db.use_database(tmp_path/'empty.sqlite3')
    assert rooms.all_rooms() == []
    assert checkin.process_noshows() == []


def test_overlap_and_adjacent_reservations():
    reservation()
    reservation(start=at(10),end=at(11))
    with pytest.raises(ValueError, match='다른 예약'):
        reservation(start=at(9,30),end=at(10,30))
    with db.tx(write=True) as conn, pytest.raises(sqlite3.IntegrityError,match='overlap'):
        conn.execute('UPDATE reservations SET start_at=? WHERE id=2',(at(9,30),))


def test_cancel_preserves_history_and_reopens_slot():
    rid=reservation()
    with pytest.raises(ValueError,match='예약자 또는 관리자'):
        bookings.cancel(rid,2)
    bookings.cancel(rid,1)
    assert rooms.room_schedule(1,at().date()) == []
    assert any(row['id']==1 for row in rooms.free_rooms(at(),at(10)))
    new_id=reservation()
    assert new_id > rid
    with db.tx() as conn:
        assert conn.execute('SELECT state FROM v_reservations WHERE id=?',(rid,)).fetchone()['state']=='취소'
        assert conn.execute('SELECT count(*) AS n FROM reservations').fetchone()['n']==2
    clock.freeze(at(9))
    assert [r['id'] for r in checkin.process_noshows()] == []


@pytest.mark.parametrize('minute,kind',[(49,'too_early'),(50,'ok')])
def test_checkin_open_boundary(minute,kind):
    reservation()
    clock.freeze(at(8,minute))
    result=checkin.check_in(rooms.all_rooms()[0],2)
    assert result.kind==kind
    if kind=='ok':
        assert result.receipt['checked_in_by']=='참석자'
        assert result.notify[0]['id']==1
        assert checkin.check_in(rooms.all_rooms()[0],2).kind=='already'
        assert checkin.check_in(rooms.all_rooms()[0],1).kind=='joined'


def test_checkin_wrong_room_and_non_attendee():
    reservation()
    clock.freeze(at(9))
    assert checkin.check_in(rooms.all_rooms()[0],3).kind=='not_attendee'
    assert checkin.check_in(rooms.all_rooms()[1],2).kind=='wrong_room'


def test_reminders_and_noshow_idempotency():
    rid=reservation()
    clock.freeze(at(8,50))
    assert [(kind,row['id']) for kind,row in checkin.due_reminders()]==[('checkin_open',rid)]
    assert checkin.due_reminders()==[]
    clock.freeze(at(9,5))
    assert [(kind,row['id']) for kind,row in checkin.due_reminders()]==[('noshow_warning',rid)]
    clock.freeze(at(9,10))
    assert [r['id'] for r in checkin.process_noshows()]==[rid]
    assert checkin.process_noshows()==[]
    assert checkin.check_in(rooms.all_rooms()[0],2).kind=='too_late'
    assert rooms.stats()['noshow']==1
    assert rooms.status_now()[0]['state']=='비어있음'


def test_checked_in_meeting_never_cancelled():
    reservation()
    clock.freeze(at(9))
    assert checkin.check_in(rooms.all_rooms()[0],2).kind=='ok'
    clock.freeze(at(9,30))
    assert checkin.process_noshows()==[]
    assert rooms.status_now()[0]['state']=='사용중'
    assert rooms.stats()['used']==1


@pytest.mark.parametrize('minute',[9,10])
def test_simultaneous_checkin_and_noshow(minute):
    reservation()
    clock.freeze(at(9,minute))
    room=rooms.all_rooms()[0]
    barrier=threading.Barrier(8)
    def run(i):
        barrier.wait(timeout=5)
        return checkin.check_in(room,2).kind if i%2 else checkin.process_noshows()
    with ThreadPoolExecutor(max_workers=8) as executor:
        results=list(executor.map(run,range(8)))
    with db.tx() as conn:
        checks=conn.execute('SELECT count(*) AS n FROM checkins').fetchone()['n']
        cancels=conn.execute('SELECT count(*) AS n FROM noshow_cancellations').fetchone()['n']
    assert (checks,cancels)==((1,0) if minute==9 else (0,1))
    assert results


def test_admin_reassignment_keeps_original_checkins():
    rid=reservation()
    rooms.link_slack_user(1,'UADMIN')
    clock.freeze(at(9))
    checkin.check_in(rooms.all_rooms()[0],2)
    reply,dms=admin.execute(rooms.all_rooms()[0],at(),at(11),'공사','UADMIN')
    assert len(dms)==2
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM checkins WHERE reservation_id=?',(rid,)).fetchone()['n']==1
        archive=conn.execute('SELECT * FROM admin_cancellations').fetchone()
        assert sorted(archive['attendee_ids'])==[1,2]
        assert archive['block_id']>rid
    checkin.process_noshows()
    assert rooms.status_now()[0]['state']=='사용중'
    assert checkin.due_reminders()==[]


def test_local_booking_commands_and_permissions():
    owner=rooms.employee_by_id(1)
    reply=commands.handle('예약 9A 내일 14:00 1h 팀 회의',owner,'U1')
    assert '예약 완료' in reply.text
    with db.tx() as conn:
        rid=conn.execute('SELECT id FROM reservations').fetchone()['id']
    assert '추가' in commands.handle(f'참석자 {rid} E2',owner,'U1').text
    assert len(rooms.attendees_of([rid])[rid])==2
    assert '예약자 또는 관리자' in commands.handle(f'취소 {rid}',rooms.employee_by_id(2),'U2').text
    assert '취소했어요' in commands.handle(f'취소 {rid}',owner,'U1').text


def test_email_requires_full_match():
    assert rooms.employee_by_email('A@example.test')['id']==1
    assert rooms.employee_by_email('a@other.test') is None


def test_room_channel_mapping_update():
    room_channels._save(1,channel_id='C1',panel_ts='123',panel_posted_at=at())
    room_channels._save(1,panel_hash='hash')
    assert room_channels.mapping()[1]['panel_hash']=='hash'
    assert room_channels.mapping()[1]['panel_posted_at']==at()


def test_midnight_timezone_boundaries():
    start=at(23,30)
    rid=reservation(start=start,end=start+timedelta(hours=1))
    assert [r['id'] for r in rooms.room_schedule(1,at().date())]==[rid]
    assert rooms.room_schedule(1,(start+timedelta(days=1)).date())==[]
    assert rooms.free_rooms(start.astimezone(timezone.utc),start+timedelta(minutes=15)) == rooms.free_rooms(start,start+timedelta(minutes=15))
    clock.freeze(at(23,45))
    assert rooms.status_now()[0]['current_reservation_id'] == rid


def test_finder_shows_results_immediately_without_capacity_or_search_submit():
    from roombot.slack_app import finder_view, _finder_values, booking_view
    from slack_sdk.models.views import View
    reservation()
    view=finder_view(at(),60)
    assert 'submit' not in view
    assert all(block['type']!='input' for block in view['blocks'])
    assert not any(block.get('block_id')=='cap' for block in view['blocks'])
    controls={block['block_id']:block['accessory'] for block in view['blocks'] if 'block_id' in block}
    assert controls['time']['type']=='static_select'
    assert len(controls['time']['options'])==18
    buttons=[block['accessory'] for block in view['blocks'] if block.get('accessory',{}).get('type')=='button']
    assert len(buttons)==1 and buttons[0]['text']['text']=='예약'
    form=booking_view(buttons[0]['value'])
    assert form['callback_id']=='booking_form'
    View(**view).validate_json()
    View(**form).validate_json()
    state={'state':{'values':{
        'date':{'finder_date':{'selected_date':'2026-11-04'}},
        'time':{'finder_time':{'selected_option':{'value':'10:00'}}},
        'dur':{'finder_dur':{'selected_option':{'value':'30'}}}}}}
    start,duration=_finder_values(state)
    assert (start,duration)==(at(10),timedelta(minutes=30))
    changed=finder_view(start,30)
    buttons=[b['accessory'] for b in changed['blocks'] if b.get('accessory',{}).get('type')=='button']
    assert len(buttons)==2


def test_finder_past_after_hours_and_default_rounding():
    from roombot.slack_app import finder_view
    assert '지난 시간' in str(finder_view(at(7)))
    assert '18시' in str(finder_view(at(17,30),60))
    clock.freeze(at(9).replace(second=1))
    assert rooms.default_start()==at(9,30)
    clock.freeze(at(19))
    view=finder_view()
    assert view['blocks'][0]['accessory']['initial_date']=='2026-11-05'
    assert view['blocks'][1]['accessory']['initial_option']['value']=='09:00'


def test_health_and_signed_schedule_image(monkeypatch):
    import io
    import time as epoch
    from PIL import Image
    from fastapi.testclient import TestClient
    from roombot import imgurl, web
    reservation()
    client=TestClient(web.app)
    assert client.get('/healthz').json()=={'ok':True}
    assert client.get('/img/board.png').status_code==404
    params={'d':'2026-11-04','e':int(epoch.time())+60,'rev':imgurl.IMAGE_VERSION}
    params['s']=imgurl._sig('board',params)
    response=client.get('/img/board.png',params=params)
    assert response.status_code==200
    image=Image.open(io.BytesIO(response.content))
    assert image.width>=2000 and image.height>0


def test_finder_and_booking_handlers_without_slack_network(monkeypatch):
    from roombot import slack_app, slackfiles
    from types import SimpleNamespace
    handlers={}
    class FakeApp:
        def __init__(self,**kwargs): pass
        def register(self,key):
            def decorator(fn):
                handlers[fn.__name__]=fn
                return fn
            return decorator
        action=command=event=view=register
    monkeypatch.setattr(slack_app,'App',FakeApp)
    monkeypatch.setattr(slackfiles,'install_guard',lambda:None)
    slack_app.build_app()
    rooms.link_slack_user(1,'U1')
    calls=[]
    state={'id':'V1','hash':'H1','state':{'values':{
        'date':{'finder_date':{'selected_date':'2026-11-04'}},
        'time':{'finder_time':{'selected_option':{'value':'09:00'}}},
        'dur':{'finder_dur':{'selected_option':{'value':'60'}}}}}}
    client=SimpleNamespace(views_update=lambda **data:calls.append(data),views_push=lambda **data:calls.append(data))
    handlers['finder_changed'](lambda **data:calls.append(data),{'view':state},client)
    assert calls[1]['view_id']=='V1' and calls[1]['hash']=='H1'
    buttons=[b['accessory'] for b in calls[1]['view']['blocks'] if b.get('accessory',{}).get('type')=='button']
    handlers['book_room'](lambda **data:None,{'user':{'id':'U1'},'trigger_id':'T1','actions':[buttons[0]]},client)
    form=calls[-1]['view']
    form['state']={'values':{'title':{'title':{'value':'앱 예약'}}}}
    answers=[]
    handlers['booking_submit'](lambda **data:answers.append(data),{'user':{'id':'U1'}},form,client)
    assert answers[-1]['response_action']=='update'
    with db.tx() as conn:
        assert conn.execute('SELECT title FROM reservations').fetchone()['title']=='앱 예약'
    handlers['booking_submit'](lambda **data:answers.append(data),{'user':{'id':'U1'}},form,client)
    assert answers[-1]['response_action']=='errors'


def test_small_wrapped_layouts_have_no_horizontal_tables(monkeypatch):
    from roombot.slack_app import home_view
    monkeypatch.setattr(settings,'image_base_url','https://images.example.test')
    reservation()
    clock.freeze(at(9,5))
    replies=[commands.status_reply(),commands.schedule_reply(rooms.all_rooms()[0],at().date()),
             commands.my_reply(rooms.employee_by_id(1)),commands.free_search(at(10),timedelta(hours=1))]
    for reply in replies:
        assert not any(block['type']=='table' for block in reply.blocks)
        assert len(reply.blocks)<50
    status=commands.status_reply()
    assert any('9A' in str(block) and block['type']=='context' for block in status.blocks)
    assert any(element.get('url','').startswith('https://images.example.test/view/')
               for block in status.blocks for element in block.get('elements',[]) if block['type']=='actions')
    assert not any(block['type']=='table' for block in home_view(rooms.employee_by_id(1))['blocks'])


def test_image_source_never_uses_unshared_bot_files(monkeypatch):
    from roombot import imgurl,slackfiles
    monkeypatch.setattr(slackfiles,'can_upload',lambda:pytest.fail('private file upload must not be used'))
    monkeypatch.setattr(settings,'image_base_url','https://images.example.test')
    monkeypatch.setattr(settings,'public_base_url','http://localhost:8000')
    source=imgurl.src('board',d='2026-11-04')
    assert source['image_url'].startswith('https://images.example.test/img/board.png?')
    assert 'slack_file' not in source
    monkeypatch.setattr(settings,'image_base_url','')
    assert imgurl.src('board',d='2026-11-04') is None


def test_image_only_host_signed_viewer_and_head(monkeypatch):
    import time as epoch
    from urllib.parse import urlparse,parse_qs
    from fastapi.testclient import TestClient
    from roombot import image_host,imgurl
    monkeypatch.setattr(settings,'image_base_url','https://images.example.test')
    client=TestClient(image_host.app)
    for path in ['/','/docs','/openapi.json','/c/9A','/demo','/auth/slack','/view/board','/img/board.png']:
        assert client.get(path).status_code==404
    assert client.get('/healthz').json()=={'ok':True}
    url=imgurl.viewer('board',d='2026-11-04')
    parsed=urlparse(url)
    params={key:values[0] for key,values in parse_qs(parsed.query).items()}
    response=client.get(parsed.path,params=params)
    assert response.status_code==200
    assert 'width=device-width' in response.text and '화면에 맞추기' in response.text
    assert '/img/board.png?' in response.text
    assert response.headers['referrer-policy']=='no-referrer'
    assert client.head('/img/board.png',params=params).status_code==200
    params['d']='2026-11-05'
    assert client.get('/view/board',params=params).status_code==404
    params['e']=str(int(epoch.time())-1)
    params['s']=imgurl._sig('board',params)
    assert client.get('/img/board.png',params=params).status_code==404


def test_simulation_excludes_owner_preserves_real_data_and_never_notifies():
    from roombot import simulations
    real = reservation(start=at(10),end=at(11))
    info = simulations.seed_week([1],at().date(),days=7,per_room=2)
    assert info['reservations'] > 0
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM reservations WHERE id=?',(real,)).fetchone()['n']==1
        assert conn.execute('SELECT count(*) AS n FROM reservation_attendees a JOIN simulation_reservations s ON s.reservation_id=a.reservation_id WHERE a.employee_id=1').fetchone()['n']==0
        assert conn.execute('SELECT count(*) AS n FROM reservations r JOIN simulation_reservations s ON s.reservation_id=r.id WHERE r.organizer_id=1').fetchone()['n']==0
        first = conn.execute('SELECT r.* FROM reservations r JOIN simulation_reservations s ON s.reservation_id=r.id ORDER BY start_at LIMIT 1').fetchone()
    clock.freeze(first['start_at']-timedelta(minutes=10))
    assert all(not r['is_simulated'] for _,r in checkin.due_reminders())
    clock.freeze(first['start_at']+timedelta(minutes=10))
    assert all(not r['is_simulated'] for r in checkin.process_noshows())
    with db.tx() as conn:
        row = conn.execute('SELECT * FROM v_reservations WHERE id=?',(first['id'],)).fetchone()
        assert row['state']=='가상 일정'
    assert rooms.stats()['total']<=1
    assert simulations.clear_batch(info['batch'])==info['reservations']
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM reservations WHERE id=?',(real,)).fetchone()['n']==1


def test_sqlite_v1_upgrade_preserves_records():
    path = db.database_path()
    with db.tx(write=True) as conn:
        conn.execute('DROP TABLE simulation_reservations')
        conn.execute('DROP TABLE simulation_batches')
        conn.execute('PRAGMA user_version=1')
    db._ready.discard(path)
    db.initialize()
    with db.tx() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()['user_version']==2
        assert conn.execute('SELECT count(*) AS n FROM employees').fetchone()['n']==3
        assert conn.execute('SELECT count(*) AS n FROM simulation_batches').fetchone()['n']==0
