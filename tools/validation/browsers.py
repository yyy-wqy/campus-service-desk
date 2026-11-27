"""Real browser form submissions against the isolated delivery database."""
import argparse
import io
import json
from pathlib import Path
import platform
import re
import sqlite3
import time

from PIL import Image
from playwright.sync_api import sync_playwright


def validate(root, base, executables):
    fixture = json.loads((root / "fixture.json").read_text())
    results = []
    with sync_playwright() as playwright:
        for index, (label, executable) in enumerate(executables.items()):
            record = {"browser": label, "executable": executable, "platform": platform.platform(), "checks": [], "started": time.time()}
            browser = None
            try:
                browser = playwright.chromium.launch(executable_path=executable, headless=True)
                record["version"] = browser.version
                contexts = [browser.new_context(viewport={"width": 1366, "height": 900}) for _ in range(4)]
                student, worker, admin, teacher = [context.new_page() for context in contexts]
                for page in (student, worker, admin, teacher): page.set_default_timeout(12000)
                password = fixture["password"]

                def check(name, condition=True):
                    if not condition: raise AssertionError(name)
                    record["checks"].append(name)

                def form(page, selector, fields):
                    target = page.locator(selector)
                    for field, value in fields.items():
                        element = target.locator(f'[name="{field}"]')
                        if element.evaluate("e => e.tagName") == "SELECT": element.select_option(str(value))
                        else: element.fill(str(value))
                    target.locator('button[type="submit"]').click()
                    page.wait_for_load_state("domcontentloaded")

                def login(page, username):
                    page.goto(base + "/accounts/login/")
                    form(page, "form.login-form", {"username": username, "password": password})
                    check("login:" + username, "/login/" not in page.url)

                def state(ticket, status):
                    with sqlite3.connect(f"{(root / 'db.sqlite3').resolve().as_uri()}?mode=ro", uri=True) as db:
                        row = db.execute("SELECT status,cancellation_pending FROM tickets_ticket WHERE id=?", (ticket,)).fetchone()
                    check("state:" + status, row[0] == status)
                    return row

                def register(page, role, number):
                    username = f"261126{number:06d}" if role == "student" else f"browser-teacher-{number}"
                    page.goto(base + "/accounts/register/")
                    page.locator('[name="role"]').select_option(role)
                    fields = {"real_name": "浏览器验收", "phone": f"138{number:08d}", "password1": password, "password2": password}
                    if role != "maintenance": fields["username"] = username
                    if role == "teacher": fields["department"] = "验收院系"
                    form(page, "form.login-form", fields)
                    check("register:" + role, "/login/" in page.url)
                    if role == "maintenance":
                        username = re.search(r"maint_[0-9a-f]{32}", page.inner_text("body")).group()
                    return username

                student_name = register(student, "student", index * 10 + 1)
                worker_name = register(worker, "maintenance", index * 10 + 2)
                teacher_name = register(teacher, "teacher", index * 10 + 3)
                login(admin, fixture["users"][15]["username"])
                admin.goto(base + "/accounts/reviews/")
                admin.get_by_role("link", name=re.compile(re.escape(teacher_name))).click()
                form(admin, "form.login-form", {"result": "approved"})
                login(teacher, teacher_name)
                login(student, student_name)
                student.goto(base + "/accounts/profile/")
                form(student, "form.login-form", {"real_name": "更新后的浏览器验收"})
                check("profile_updated", "个人资料已更新" in student.inner_text("body"))
                login(worker, worker_name)
                denied = worker.goto(base + "/tickets/tasks/")
                check("unapproved_worker_denied", denied.status == 403)
                worker.goto(base + "/accounts/qualifications/")
                worker.get_by_role("button", name="材料齐全，提交审核").click()
                check("missing_materials_rejected", "每类需要1至3份" in worker.inner_text("body"))
                image = io.BytesIO(); Image.new("RGB", (16, 16), "white").save(image, format="PNG")

                def upload(category):
                    worker.goto(base + "/accounts/qualifications/")
                    target = worker.locator('form[action$="/qualifications/upload/"]')
                    target.locator('[name="category"]').select_option(category)
                    if category == "qualification":
                        target.locator('[name="name"]').fill("验收电工资质")
                        target.locator('[name="direction"]').fill("水电")
                    target.locator('[name="attachment"]').set_input_files({"name": "proof.png", "mimeType": "image/png", "buffer": image.getvalue()})
                    target.locator('button[type="submit"]').click()

                for _ in range(3): upload("employment")
                upload("employment")
                check("fourth_material_rejected", "每类最多3份" in worker.inner_text("body"))
                upload("qualification")
                worker.get_by_role("button", name="材料齐全，提交审核").click()
                admin.goto(base + "/accounts/maintenance-reviews/")
                admin.get_by_role("link", name=re.compile(re.escape(worker_name))).click()
                review_url = admin.url
                form(admin, 'form.login-form:not([action])', {"result": "needs_more", "reason": "请补充清晰材料"})
                worker.goto(base + "/accounts/qualifications/")
                check("supplement_login_and_reason", "请补充清晰材料" in worker.inner_text("body"))
                worker.get_by_role("button", name="移出本轮材料（历史保留）").first.click()
                upload("employment")
                worker.get_by_role("button", name="材料齐全，提交审核").click()
                admin.goto(review_url)
                form(admin, 'form.login-form:not([action])', {"result": "approved"})
                check("worker_approved", "已通过" in admin.inner_text("body"))

                def create_ticket():
                    student.goto(base + "/tickets/create/")
                    form(student, "form.ticket-form", {"title": "浏览器闭环验收", "description": "灯具无法点亮",
                         "building": fixture["building"], "fault_category": fixture["category"],
                         "location_kind": "room", "room_number": "A101"})
                    check("ticket_created", bool(re.search(r"/tickets/\d+/$", student.url)))
                    return int(student.url.rstrip("/").split("/")[-1]), student.url

                def assign(url):
                    admin.goto(url)
                    choice = admin.locator('select[name="worker"] option').filter(has_text=worker_name).get_attribute("value")
                    form(admin, 'form[action$="/accept/"]', {"worker": choice})

                ticket, url = create_ticket(); assign(url)
                worker.goto(url)
                form(worker, 'form[action$="/repair/start/"]', {})
                form(worker, 'form[action$="/repair/progress/"]', {"content": "检查线路"})
                form(worker, 'form[action$="/repair/result/"]', {"content": "第一轮维修结果"})
                state(ticket, "pending_confirmation")
                student.goto(url)
                form(student, 'form[action$="/repair/confirm/"]', {"decision": "unresolved", "reason": "仍未解决"})
                state(ticket, "in_progress")
                worker.goto(url)
                form(worker, 'form[action$="/repair/result/"]', {"content": "第二轮维修结果"})
                student.goto(url)
                form(student, 'form[action$="/repair/confirm/"]', {"decision": "resolved"})
                state(ticket, "completed")
                check("all_rounds_retained", all(t in student.inner_text("body") for t in ["第一轮维修结果", "第二轮维修结果", "仍未解决"]))

                ticket, url = create_ticket(); assign(url)
                worker.goto(url); form(worker, 'form[action$="/repair/start/"]', {})
                student.goto(base + f"/tickets/{ticket}/cancel/")
                form(student, "form.ticket-form", {"reason": "另行安排"})
                worker.goto(url)
                check("pending_freezes_worker_buttons", worker.locator('form[action*="/repair/"]').count() == 0)
                check("pending_keeps_notes", worker.locator('form[action$="/notes/"]').count() == 1)
                form(worker, 'form[action$="/notes/"]', {"text": "取消待审仍可说明"})
                admin.goto(url); admin.get_by_role("link", name="审核这条申请").click()
                form(admin, "form.ticket-form", {"decision": "rejected", "reason": "继续维修"})
                worker.goto(url)
                check("rejection_restores_actions", worker.locator('form[action$="/repair/progress/"]').count() == 1)
                student.goto(base + f"/tickets/{ticket}/cancel/"); form(student, "form.ticket-form", {"reason": "确认取消"})
                admin.goto(url); admin.get_by_role("link", name="审核这条申请").click()
                form(admin, "form.ticket-form", {"decision": "approved"})
                state(ticket, "cancelled")
                student.goto(url)
                check("cancellation_history_retained", all(t in student.inner_text("body") for t in ["另行安排", "继续维修", "确认取消", "取消待审仍可说明"]))
                ticket, url = create_ticket()
                admin.goto(url); form(admin, 'form[action$="/reject/"]', {"reason": "范围不符"})
                student.goto(url)
                check("rejection_read_only", student.locator('form[action$="/notes/"]').count() == 0)
                denied = student.goto(base + "/tickets/cancellations/")
                check("reporter_admin_page_forbidden", denied.status == 403)
                student.goto(base + "/accounts/login/")
                student.screenshot(path=str(root / f"browser-{label}.png"), full_page=True)
                record["passed"] = True
            except Exception as error:
                record["passed"] = False
                record["error_type"] = type(error).__name__
                record["error"] = str(error)[:1500]
            finally:
                if browser: browser.close()
                record["finished"] = time.time()
                results.append(record)
                (root / "browser-results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
                print(json.dumps({k:v for k,v in record.items() if k not in {"executable","checks"}},ensure_ascii=False),flush=True)
    return all(r["passed"] for r in results)


if __name__ == "__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--root",type=Path,required=True);parser.add_argument("--executables",type=Path,required=True);parser.add_argument("--base",default="http://127.0.0.1:8765")
    args=parser.parse_args()
    raise SystemExit(0 if validate(args.root,args.base,json.loads(args.executables.read_text(encoding="utf-8"))) else 1)
