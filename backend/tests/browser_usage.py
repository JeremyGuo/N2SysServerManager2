"""Additional checks called by browser_smoke.py with its isolated backend."""


def usage_checks(browser, page, base, password, errors):
    page.set_viewport_size({"width":1440,"height":1000})
    page.goto(base+"/server/1")
    page.get_by_text("AMD EPYC Browser",exact=False).first.wait_for()
    page.get_by_text("NVIDIA H100 Browser",exact=True).wait_for()
    page.get_by_text("100 Gbps",exact=False).first.wait_for()
    page.goto(base+"/apply")
    page.get_by_role("heading",name="申请机器 · 使用登记").wait_for()
    page.get_by_role("checkbox",name="选择 compute-test",exact=True).locator("xpath=ancestor::label").click()
    page.get_by_role("textbox",name="申请原因",exact=True).fill("Admin test registration")
    page.get_by_role("button",name="提交申请 / 使用登记",exact=True).click()
    compute_row=page.get_by_role("row").filter(has=page.get_by_role("link",name="compute-test",exact=True))
    compute_row.get_by_role("button",name="确认仍在使用",exact=True).wait_for()
    compute_row.get_by_role("button",name="结束使用登记",exact=True).click()
    page.get_by_role("button",name="仅结束登记",exact=True).click()
    compute_row.get_by_role("button",name="确认仍在使用",exact=True).wait_for(state="detached")
    member_context=browser.new_context(viewport={"width":1440,"height":1000})
    try:
        login_result=member_context.request.post(base+"/api/auth/login",form={"username":"member","password":password})
        assert login_result.ok
        member_page=member_context.new_page()
        member_page.on("pageerror",lambda error: errors.append(str(error)))
        member_page.goto(base+"/apply")
        member_page.get_by_role("checkbox",name="选择 compute-test",exact=True).locator("xpath=ancestor::label").click()
        member_page.get_by_role("checkbox",name="选择 empty-test",exact=True).locator("xpath=ancestor::label").click()
        member_page.get_by_role("textbox",name="申请原因",exact=True).fill("Browser batch purpose")
        member_page.get_by_role("button",name="提交申请 / 使用登记",exact=True).click()
        member_page.get_by_role("button",name="取消待审批申请",exact=True).first.wait_for()
        assert member_page.get_by_role("button",name="取消待审批申请",exact=True).count()==2
        empty_row=member_page.get_by_role("row").filter(has=member_page.get_by_role("link",name="empty-test",exact=True))
        empty_row.get_by_role("button",name="取消待审批申请",exact=True).click()
        member_page.wait_for_timeout(300)
        if member_page.get_by_role("button",name="确定",exact=True).is_visible():
            member_page.get_by_role("button",name="确定",exact=True).click()
        empty_row.get_by_role("button",name="取消待审批申请",exact=True).wait_for(state="detached")
        page.goto(base+"/management")
        page.get_by_text("Browser batch purpose",exact=True).wait_for()
        apps=page.request.get(base+"/api/app/pendings").json()
        assert len(apps)==1
        page.get_by_role("button",name="Approve",exact=True).first.click()
        page.get_by_text("Browser batch purpose",exact=True).wait_for(state="detached")
        member_page.reload()
        member_compute=member_page.get_by_role("row").filter(has=member_page.get_by_role("link",name="compute-test",exact=True))
        member_compute.get_by_role("button",name="确认仍在使用",exact=True).wait_for()
        member_compute.get_by_role("button",name="确认仍在使用",exact=True).click()
        member_page.wait_for_timeout(300)
        # Other users see the active registration too, not just their own requests.
        page.goto(base+"/apply")
        page.get_by_text("Browser Member",exact=True).wait_for()
        member_compute.get_by_role("button",name="结束使用登记",exact=True).click()
        member_page.get_by_role("button",name="仅结束登记",exact=True).click()
        member_compute.get_by_role("button",name="确认仍在使用",exact=True).wait_for(state="detached")
        usage=member_page.request.get(base+"/api/usage/devices").json()
        registered=next(d for d in usage if d["host"]=="compute-test")
        assert registered["usage"]["my_usage"]["status"]=="ended"
        assert registered["usage"]["has_account"] is True
        assert not any(u["realname"]=="Browser Member" for u in registered["usage"]["active_users"])
        print("Hardware and usage browser checks passed: hardware fields, bulk selection/apply, cancel, approval, shared usage visibility, confirmation, ending without revoking account.")
    finally:
        member_context.close()
