"""Optional real-browser smoke test, isolated temporary DB; never uses real SSH/AI.
Run from root: .venv/bin/python3 backend/tests/browser_smoke.py
Requires: pip install playwright; python3 -m playwright install chromium; npm ci.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait(url):
    for _ in range(100):
        try:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + ('/' if url.count('/') == 2 else ''), timeout=1).close()
            return
        except Exception as error:
            last_error = repr(error)
            time.sleep(.1)
    raise RuntimeError(f"Test service failed to start: {url}: {last_error}")


def main():
    with tempfile.TemporaryDirectory(prefix="n2sys-browser-") as tmp:
        password = secrets.token_urlsafe(18)
        env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp}/test.db", "SECRET_KEY": secrets.token_urlsafe(48), "INITIAL_ADMIN_PASSWORD": password, "SYNC_ENABLED": "false", "AI_BASE_URL": "", "AI_MODEL": ""}
        backend_port, frontend_port = port(), port()
        # Test-only Vite configuration points at this isolated backend.
        config = ROOT / "frontend" / ".smoke-vite.config.mjs"
        config.write_text("import {defineConfig} from 'vite'; import vue from '@vitejs/plugin-vue'; export default defineConfig({plugins:[vue()],server:{proxy:{'/api':{target:'http://127.0.0.1:"+str(backend_port)+"',rewrite:p=>p.replace(/^\\/api/,'')}}}})")
        processes = []
        try:
            with open(Path(tmp)/"services.log", "w") as logs:
                processes.append(subprocess.Popen([sys.executable,"-m","uvicorn","main:app","--port",str(backend_port)],cwd=ROOT/"backend",env=env,stdout=logs,stderr=logs))
                wait(f"http://127.0.0.1:{backend_port}/docs")
                seed = '''from app.database import *
from datetime import datetime,timedelta,timezone
from app.api.auth import get_password_hash
from app.hardware_store import save_hardware
import os
with SessionLocal() as db:
    admin=db.query(User).first()
    old=User(username="oldadmin",realname="Old Admin",account_name="oldadmin",mail="old@example.com",password="disabled-test-hash",public_key="",is_admin=True,status=UserStatus.ACTIVE)
    s=Server(host="compute-test",port=22,server_status=ServerStatus.ACTIVE)
    empty=Server(host="empty-test",port=22,server_status=ServerStatus.ACTIVE)
    member=User(username="member",realname="Browser Member",account_name="member",mail="member@example.com",password=get_password_hash(os.environ["INITIAL_ADMIN_PASSWORD"]),public_key="",status=UserStatus.ACTIVE)
    db.add_all([old,s,empty,member]);db.flush()
    db.add_all([Account(user=old,server=s,last_login_date=datetime.now()-timedelta(days=60)),Account(user=admin,server=s),ServerInterface(server=s,interface="eth0",manufacturer="Mellanox",pci_address="0000:00:01.0")]);db.commit()
    now=datetime.now(timezone.utc).isoformat()
    data={"cpu":{"model":"AMD EPYC Browser","sockets":2,"cores":64,"threads":128},"memory":{"total_bytes":256*2**30,"available_bytes":128*2**30},"gpus":[{"name":"NVIDIA H100 Browser","vendor":"NVIDIA","memory_total_bytes":80*2**30,"pci_address":"0000:01:00.0"}],"disks":[{"name":"nvme0n1","model":"Browser NVMe","size_bytes":2*2**40,"type":"disk","rotational":False}],"network":[{"name":"eth0","mac":None,"speed_mbps":100000,"state":"up","pci_address":None,"error":None}]}
    save_hardware(db,s.id,{name:{"status":"ok","data":value,"error":None,"collected_at":now,"checked_at":now} for name,value in data.items()})
    db.commit()
'''
                subprocess.run([sys.executable,"-c",seed],cwd=ROOT/"backend",env=env,check=True,stdout=logs,stderr=logs)
                processes.append(subprocess.Popen(["node","node_modules/vite/bin/vite.js","--config",str(config),"--host","127.0.0.1","--port",str(frontend_port)],cwd=ROOT/"frontend",stdout=logs,stderr=logs))
                base = f"http://127.0.0.1:{frontend_port}"
                wait(base)
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True, **({'channel':os.environ['BROWSER_CHANNEL']} if os.getenv('BROWSER_CHANNEL') else {}))
                    page=browser.new_page(viewport={"width":1280,"height":850})
                    errors=[]
                    page.on("pageerror",lambda error: errors.append(str(error)))
                    page.goto(base+"/login")
                    page.get_by_placeholder("Username",exact=True).fill("admin")
                    page.get_by_placeholder("Password",exact=True).fill("wrong")
                    page.get_by_role("button",name="Sign in",exact=True).click()
                    page.get_by_role("alert").filter(has_text="用户名或密码错误").wait_for()
                    page.wait_for_timeout(3500)
                    assert page.get_by_role("alert").filter(has_text="用户名或密码错误").is_visible()
                    page.get_by_role("button",name="Dismiss error").click()
                    page.get_by_placeholder("Password",exact=True).fill(password)
                    page.get_by_role("button",name="Sign in",exact=True).click()
                    page.wait_for_url(base+"/")
                    page.get_by_text("已隐藏 1 个账号",exact=False).wait_for()
                    assert page.get_by_text("empty-test",exact=True).is_visible()
                    assert not page.get_by_role("row").filter(has_text="Old Admin").count()
                    page.get_by_role("button",name="显示全部管理员").click()
                    page.get_by_role("row").filter(has_text="Old Admin").wait_for()
                    # A legacy hide:false preference must not override default-on.
                    page.evaluate("localStorage.setItem('n2sys-summary-filter-1', JSON.stringify({hide:false,days:30}))")
                    page.reload()
                    page.get_by_text("已隐藏 1 个账号",exact=False).wait_for()
                    assert not page.get_by_role("row").filter(has_text="Old Admin").count()
                    page.goto(base+"/servers")
                    page.get_by_placeholder("按网卡 Manufacturer 查询").fill("no-match")
                    page.get_by_role("button",name="查询网卡").click()
                    page.get_by_text("没有匹配的网卡，请尝试其他厂商或型号。").wait_for()
                    page.get_by_role("button",name="打开设备 AI 聊天").click()
                    page.get_by_text("AI 尚未配置",exact=False).wait_for()
                    assert page.get_by_role("button",name="发送",exact=True).is_disabled()
                    page.route("**/api/ai/status",lambda route: route.fulfill(json={"enabled":True,"reason":"test"}))
                    page.get_by_role("button",name="检查配置").click()
                    page.get_by_placeholder("询问组内设备",exact=False).fill("我们组有哪些设备？")
                    captured=[]
                    def answer(route):
                        captured.append(route.request.post_data_json)
                        route.fulfill(json={"answer":"compute-test (ID 1) <script>bad()</script>","model":"mock"})
                    page.route("**/api/ai/chat",answer)
                    page.get_by_role("button",name="发送",exact=True).click()
                    page.get_by_text("compute-test (ID 1) <script>bad()</script>",exact=True).wait_for()
                    assert captured[0]["messages"][-1]["content"] == "我们组有哪些设备？"
                    assert page.locator(".message script").count()==0
                    page.unroute("**/api/ai/chat")
                    page.route("**/api/ai/chat",lambda route: route.fulfill(status=504,json={"detail":"AI 测试超时，请稍后重试","request_id":"smoke-timeout"}))
                    page.get_by_placeholder("询问组内设备",exact=False).fill("重试问题")
                    page.get_by_role("button",name="发送",exact=True).click()
                    page.get_by_role("alert").filter(has_text="AI 测试超时").first.wait_for()
                    assert page.get_by_placeholder("询问组内设备",exact=False).input_value()=="重试问题"
                    assert not page.get_by_role("button",name="发送",exact=True).is_disabled()
                    page.get_by_role("button",name="Dismiss error").click()
                    page.set_viewport_size({"width":390,"height":844})
                    bounds=page.locator(".chat-panel").bounding_box()
                    assert bounds["x"]>=0 and bounds["x"]+bounds["width"]<=391
                    page.screenshot(path=str(Path(tmp)/"chat-mobile.png"))
                    page.goto(base+"/devices")
                    page.get_by_role("button",name="打开设备 AI 聊天").wait_for()
                    from browser_usage import usage_checks
                    usage_checks(browser, page, base, password, errors)
                    from browser_backports import backport_checks
                    backport_checks(browser, page, base, password, errors)
                    assert not errors, errors
                    browser.close()
                    print("Browser smoke passed: persistent errors, login, summary filter/persistence, empty servers, search, AI configuration/chat/retry/plain text/mobile, Devices entry.")
        except Exception:
            print((Path(tmp)/"services.log").read_text())
            raise
        finally:
            for process in reversed(processes):
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill();process.wait()
            config.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
