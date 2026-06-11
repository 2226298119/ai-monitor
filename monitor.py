"""
monitor.py — erp.bjjcz.cn 健康检测

四层检测：
  1. 连通层   TCP 443 能否连通
  2. 页面层   首页 HTML 能否正常返回
  3. 登录层   POST /login 能否拿到 adminToken
  4. 业务层   用 token 调考勤查询接口，验证核心功能

环境变量：
  ERP_USERNAME      登录账号
  ERP_PASSWORD      登录密码
  SERVERCHAN_KEY    Server酱 SendKey（微信通知），留空不推送
  NOTIFY_EMAIL      收件邮箱，留空不发邮件
  QQ_AUTH_CODE      QQ邮箱授权码
  EMAIL_SENDER      发件QQ邮箱
"""

import os
import sys
import time
import socket
import smtplib
from datetime import datetime, date
from email.mime.text import MIMEText

import requests

BASE_URL  = "https://erp.bjjcz.cn"
HOST      = "erp.bjjcz.cn"
PORT      = 443
TIMEOUT   = int(os.environ.get("TIMEOUT", "8"))
SLOW_MS   = int(os.environ.get("SLOW_MS", "5000"))

USERNAME       = os.environ.get("ERP_USERNAME", "")
PASSWORD       = os.environ.get("ERP_PASSWORD", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")
NOTIFY_EMAIL   = os.environ.get("NOTIFY_EMAIL", "")
QQ_AUTH_CODE   = os.environ.get("QQ_AUTH_CODE", "")
EMAIL_SENDER   = os.environ.get("EMAIL_SENDER", "")


# ── 四层检测 ──────────────────────────────────────────────────

def check_connectivity():
    start = time.monotonic()
    try:
        sock = socket.create_connection((HOST, PORT), timeout=TIMEOUT)
        sock.close()
        ms = int((time.monotonic() - start) * 1000)
        return {"layer": "连通层", "ok": True, "ms": ms, "detail": f"TCP {HOST}:{PORT} 连通"}
    except socket.timeout:
        return {"layer": "连通层", "ok": False, "ms": -1, "detail": "TCP 连接超时"}
    except socket.gaierror:
        return {"layer": "连通层", "ok": False, "ms": -1, "detail": "DNS 解析失败，域名无法访问"}
    except Exception as e:
        return {"layer": "连通层", "ok": False, "ms": -1, "detail": f"连接失败: {e}"}


def check_homepage():
    start = time.monotonic()
    try:
        resp = requests.get(f"{BASE_URL}/index.html", timeout=TIMEOUT, allow_redirects=True)
        ms = int((time.monotonic() - start) * 1000)
        ok = resp.status_code == 200 and len(resp.text) > 100
        detail = f"HTTP {resp.status_code}" if ok else f"HTTP {resp.status_code}（内容异常）"
        return {"layer": "页面层", "ok": ok, "ms": ms, "detail": detail}
    except requests.exceptions.Timeout:
        return {"layer": "页面层", "ok": False, "ms": -1, "detail": "请求超时"}
    except Exception as e:
        return {"layer": "页面层", "ok": False, "ms": -1, "detail": str(e)}


def check_login():
    start = time.monotonic()
    try:
        resp = requests.post(
            f"{BASE_URL}/login",
            json={"username": USERNAME, "password": PASSWORD},
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        ms = int((time.monotonic() - start) * 1000)
        body = resp.json()
        token = (body.get("data") or {}).get("adminToken")
        ok = body.get("code") == 0 and bool(token)
        detail = (
            f"登录成功 HTTP {resp.status_code}"
            if ok
            else f"登录失败 code={body.get('code')} msg={body.get('msg')}"
        )
        return {"layer": "登录层", "ok": ok, "ms": ms, "detail": detail}, token if ok else None
    except requests.exceptions.Timeout:
        return {"layer": "登录层", "ok": False, "ms": -1, "detail": "登录超时"}, None
    except Exception as e:
        return {"layer": "登录层", "ok": False, "ms": -1, "detail": str(e)}, None


def check_business(token):
    today = date.today().strftime("%Y-%m-%d")
    start = time.monotonic()
    try:
        resp = requests.get(
            f"{BASE_URL}/examine/attendanceSign/getUserSignDetail/{today}",
            headers={"Admin-Token": token, "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        ms = int((time.monotonic() - start) * 1000)
        body = resp.json()
        ok = body.get("code") == 0
        detail = (
            f"考勤查询成功 HTTP {resp.status_code}"
            if ok
            else f"考勤查询失败 code={body.get('code')} msg={body.get('msg')}"
        )
        return {"layer": "业务层", "ok": ok, "ms": ms, "detail": detail}
    except requests.exceptions.Timeout:
        return {"layer": "业务层", "ok": False, "ms": -1, "detail": "请求超时"}
    except Exception as e:
        return {"layer": "业务层", "ok": False, "ms": -1, "detail": str(e)}


def logout(token):
    try:
        requests.post(
            f"{BASE_URL}/loginOut",
            headers={"Admin-Token": token},
            timeout=5,
        )
    except Exception:
        pass


# ── 推送 ──────────────────────────────────────────────────────

def push_serverchan(title, content):
    if not SERVERCHAN_KEY:
        return
    try:
        r = requests.get(
            f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send",
            params={"title": title, "desp": content},
            timeout=10,
        )
        print(f"[Server酱] {'成功' if r.json().get('code') == 0 else '异常'}")
    except Exception as e:
        print(f"[Server酱] 推送失败: {e}")


def push_email(subject, content):
    if not all([NOTIFY_EMAIL, QQ_AUTH_CODE, EMAIL_SENDER]):
        return
    # 支持逗号分隔多个收件人
    recipients = [r.strip() for r in NOTIFY_EMAIL.split(",") if r.strip()]
    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = ", ".join(recipients)
        with smtplib.SMTP_SSL("smtp.qq.com", 465) as s:
            s.login(EMAIL_SENDER, QQ_AUTH_CODE)
            s.sendmail(EMAIL_SENDER, recipients, msg.as_string())
        print(f"[邮件] 推送成功 → {len(recipients)}人")
    except Exception as e:
        print(f"[邮件] 推送失败: {e}")


def do_notify(title, content):
    push_serverchan(title, content)
    push_email(title, content)


# ── 主逻辑 ────────────────────────────────────────────────────

def main():
    if not USERNAME or not PASSWORD:
        print("❌ 未设置 ERP_USERNAME / ERP_PASSWORD")
        sys.exit(1)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}\n检测时间: {now_str}\n目标: {BASE_URL}\n{'='*50}")

    results = []
    token = None

    # 第一层：连通
    r1 = check_connectivity()
    results.append(r1)
    print(f"[{r1['layer']}]  {'✅' if r1['ok'] else '❌'}  {r1['detail']}  ({r1['ms']}ms)" )

    if not r1["ok"]:
        # 连通失败，后面没必要测了
        for layer in ["页面层", "登录层", "业务层"]:
            results.append({"layer": layer, "ok": False, "ms": -1, "detail": "跳过（连通层失败）"})
            print(f"[{layer}]  ⏭  跳过（连通层失败）")
    else:
        # 第二层：页面
        r2 = check_homepage()
        results.append(r2)
        print(f"[{r2['layer']}]  {'✅' if r2['ok'] else '❌'}  {r2['detail']}  ({r2['ms']}ms)")

        # 第三层：登录
        r3, token = check_login()
        results.append(r3)
        print(f"[{r3['layer']}]  {'✅' if r3['ok'] else '❌'}  {r3['detail']}  ({r3['ms']}ms)")

        # 第四层：业务
        if token:
            r4 = check_business(token)
            results.append(r4)
            print(f"[{r4['layer']}]  {'✅' if r4['ok'] else '❌'}  {r4['detail']}  ({r4['ms']}ms)")
            logout(token)
        else:
            r4 = {"layer": "业务层", "ok": False, "ms": -1, "detail": "跳过（登录失败无 token）"}
            results.append(r4)
            print(f"[{r4['layer']}]  ⏭  {r4['detail']}")

    failed = [r for r in results if not r["ok"] and "跳过" not in r["detail"]]
    slow   = [r for r in results if r["ok"] and r["ms"] > SLOW_MS]

    print()
    if not failed and not slow:
        print("✅ 全部正常，不推送")
        sys.exit(0)

    # 组装告警
    if failed:
        names = " + ".join(r["layer"] for r in failed)
        title = f"🔴 ERP告警 | {names} 异常"
    else:
        names = " + ".join(r["layer"] for r in slow)
        title = f"⚠️ ERP响应慢 | {names} 超过{SLOW_MS}ms"

    lines = [f"时间: {now_str}", f"地址: {BASE_URL}", "", "各层检测结果:"]
    for r in results:
        icon = "✅" if r["ok"] else ("⏭" if "跳过" in r["detail"] else "❌")
        ms_str = f"{r['ms']}ms" if r["ms"] > 0 else "-"
        lines.append(f"  {icon} {r['layer']}: {r['detail']} ({ms_str})")

    content = "\n".join(lines)
    print(title)
    do_notify(title, content)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()