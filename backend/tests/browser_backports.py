"""Real browser, isolated backend: restored features without any remote operations."""
from urllib.parse import urlsplit
from playwright.sync_api import expect


def sign_in(page, username, password):
    page.get_by_placeholder('Username',exact=True).fill(username)
    page.get_by_placeholder('Password',exact=True).fill(password)
    page.get_by_role('button',name='Sign in',exact=True).click()


def backport_checks(browser,page,base,password,errors):
    # Current admin session created by browser_smoke.py. Register a test applicant
    # through the real public endpoint, then exercise the explicit reject dialog.
    response=page.request.post(base+'/api/auth/register',data={
        'username':'rejecttest','realname':'Reject Test Real Name','account_name':'rejecttest',
        'mail':'rejecttest@example.com','public_key':'','password':'test-only-strong-password'
    })
    assert response.status==201,response.text()
    page.goto(base+'/management')
    applicant=page.get_by_role('row').filter(has_text='Reject Test Real Name')
    applicant.get_by_role('button',name='Reject Registration',exact=True).click()
    page.get_by_role('button',name='Keep registration',exact=True).click()
    assert applicant.count()==1
    applicant.get_by_role('button',name='Reject Registration',exact=True).click()
    page.get_by_role('button',name='Reject registration',exact=True).click()
    applicant.wait_for(state='detached')
    page.goto(base+'/')
    page.get_by_role('columnheader',name='Linux Account Name',exact=True).wait_for()

    # No-change preflight sends no update request. Profile draft survives only a
    # same-tab login by the same identity, never the typed password.
    me=page.request.get(base+'/api/user/me').json()
    page.goto(base+f"/profile/{me['id']}")
    page.get_by_role('button',name='Save Changes',exact=True).wait_for()
    page.wait_for_timeout(200)
    updates=[]
    page.on('request',lambda request:updates.append(request.url) if '/api/user/update' in request.url else None)
    page.get_by_role('button',name='Save Changes',exact=True).click()
    page.get_by_text('No changes to save',exact=False).wait_for()
    assert not updates
    realname=page.locator('.el-form-item').filter(has=page.locator('label',has_text='Real Name')).get_by_role('textbox')
    realname.fill('Unsaved same-user draft')
    page.get_by_role('button',name='Save Changes',exact=True).click()
    page.get_by_text('Current password is required',exact=False).first.wait_for()
    assert not updates
    page.get_by_role('textbox',name='Current password (required to save changes)',exact=True).fill('do-not-restore-secret')
    page.route('**/api/user/update',lambda route:route.fulfill(status=401,json={'detail':'Session expired test'}))
    page.get_by_role('button',name='Save Changes',exact=True).click()
    page.wait_for_url('**/login?**')
    page.get_by_text('Your session has expired.',exact=False).wait_for()
    page.unroute('**/api/user/update')
    sign_in(page,'admin',password)
    page.wait_for_url(base+f"/profile/{me['id']}")
    realname=page.locator('.el-form-item').filter(has=page.locator('label',has_text='Real Name')).get_by_role('textbox')
    expect(realname).to_have_value('Unsaved same-user draft')
    assert page.get_by_role('textbox',name='Current password (required to save changes)',exact=True).input_value()==''
    # Explicit logout clears the retained draft.
    page.get_by_role('button',name='Logout',exact=True).click()
    page.wait_for_url(base+'/login')
    sign_in(page,'admin',password)
    page.wait_for_url(base+'/')
    page.goto(base+f"/profile/{me['id']}")
    realname=page.locator('.el-form-item').filter(has=page.locator('label',has_text='Real Name')).get_by_role('textbox')
    realname.wait_for()
    page.wait_for_timeout(200)
    assert realname.input_value()!= 'Unsaved same-user draft'

    member_context=browser.new_context(viewport={'width':1440,'height':1000})
    try:
        assert member_context.request.post(base+'/api/auth/login',form={'username':'member','password':password}).ok
        member=member_context.new_page()
        member.on('pageerror',lambda error:errors.append(str(error)))
        member.goto(base+'/apply')
        member.get_by_role('checkbox',name='选择 compute-test',exact=True).locator('xpath=ancestor::label').click()
        member.get_by_role('textbox',name='申请原因',exact=True).fill('new active registration')
        member.get_by_role('button',name='提交申请 / 使用登记',exact=True).click()
        row=member.get_by_role('row').filter(has=member.get_by_role('link',name='compute-test',exact=True))
        row.get_by_role('button',name='申请 sudo 权限',exact=True).wait_for()
        record=next(d for d in member.request.get(base+'/api/usage/devices').json() if d['host']=='compute-test')['usage']['my_usage']
        for action in ('cancel','approve'):
            row.get_by_role('button',name='申请 sudo 权限',exact=True).click()
            member.get_by_role('textbox',name='sudo 申请原因',exact=True).fill('Browser sudo reason')
            member.get_by_role('button',name='提交 sudo 申请',exact=True).click()
            row.get_by_role('button',name='取消 sudo 申请',exact=True).wait_for()
            if action=='cancel':
                row.get_by_role('button',name='取消 sudo 申请',exact=True).click()
                row.get_by_role('button',name='申请 sudo 权限',exact=True).wait_for()
            else:
                page.goto(base+'/management')
                pending=page.get_by_role('row').filter(has_text='Browser sudo reason')
                pending.get_by_role('button',name='Approve',exact=True).click()
                pending.wait_for(state='detached')
        member.reload()
        row=member.get_by_role('row').filter(has=member.get_by_role('link',name='compute-test',exact=True))
        row.get_by_text('已有 sudo 权限',exact=True).wait_for()
        result=next(d for d in member.request.get(base+'/api/usage/devices').json() if d['host']=='compute-test')['usage']
        assert result['my_usage']['status']=='active' and result['has_sudo'] is True
        assert result['my_usage']['started_at']==record['started_at']
        # Application draft restoration and public registration-page exemption.
        member.get_by_role('checkbox',name='选择 empty-test',exact=True).locator('xpath=ancestor::label').click()
        member.get_by_role('textbox',name='申请原因',exact=True).fill('keep unsaved machine request')
        member.route('**/api/usage/apply',lambda route:route.fulfill(status=401,json={'detail':'Expired test'}))
        member.get_by_role('button',name='提交申请 / 使用登记',exact=True).click()
        member.wait_for_url('**/login?**')
        member.unroute('**/api/usage/apply')
        sign_in(member,'member',password)
        member.wait_for_url(base+'/apply')
        expect(member.get_by_role('textbox',name='申请原因',exact=True)).to_have_value('keep unsaved machine request')
        assert member.get_by_role('checkbox',name='选择 empty-test',exact=True).is_checked()
    finally:
        member_context.close()
    anonymous=browser.new_context()
    try:
        guest=anonymous.new_page()
        guest.goto(base+'/register')
        guest.wait_for_timeout(300)
        assert urlsplit(guest.url).path=='/register'
    finally:
        anonymous.close()
    print('Backport browser checks passed: reject registration, real/account names, safe401 navigation, profile/apply draft restoration, public registration access, active sudo request cancel/approve.')
