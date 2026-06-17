"""
monitor.py — 多网站健康检测

当前检测目标：
  1. ERP系统      https://erp.bjjcz.cn      四层检测
  2. 激光云平台   https://www.laser-cloud.cn  登录检测

推送规则：
  - 企业微信：每次都发，正常绿色，慢响应橙色，异常红色
  - 邮件/Server酱：仅异常时发

异常定义：接口调用失败、超时、返回非成功状态码
慢响应定义：成功但响应时间超过 SLOW_MS（默认8000ms）
"""

import os
import sys
import time
import socket
import smtplib
from datetime import datetime, date
from email.mime.text import MIMEText
import requests

TIMEOUT        = int(os.environ.get("TIMEOUT", "8"))
SLOW_MS        = int(os.environ.get("SLOW_MS", "8000"))

ERP_USERNAME   = os.environ.get("ERP_USERNAME", "")
ERP_PASSWORD   = os.environ.get("ERP_PASSWORD", "")
LC_USERNAME    = os.environ.get("LC_USERNAME", "")
LC_PASSWORD    = os.environ.get("LC_PASSWORD", "")

WECOM_WEBHOOK  = os.environ.get("WECOM_WEBHOOK", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")
NOTIFY_EMAIL   = os.environ.get("NOTIFY_EMAIL", "")
QQ_AUTH_CODE   = os.environ.get("QQ_AUTH_CODE", "")
EMAIL_SENDER   = os.environ.get("EMAIL_SENDER", "")


# ── 通用检测函数 ──────────────────────────────────────────────

def check_tcp(host, port):
    start = time.monotonic()
    try:
        sock = socket.create_connection((host, port), timeout=TIMEOUT)
        sock.close()
        ms = int((time.monotonic() - start) * 1000)
        return {"layer": "连通层", "ok": True, "ms": ms, "detail": f"TCP {host}:{port} 连通"}
    except socket.timeout:
        return {"layer": "连通层", "ok": False, "ms": -1, "detail": "TCP 连接超时"}
    except socket.gaierror:
        return {"layer": "连通层", "ok": False, "ms": -1, "detail": "DNS 解析失败"}
    except Exception as e:
        return {"layer": "连通层", "ok": False, "ms": -1, "detail": f"连接失败: {e}"}


def check_page(url):
    start = time.monotonic()
    try:
        resp = requests.get(url, timeout=TIMEOUT, allow_redirects=True)
        ms = int((time.monotonic() - start) * 1000)
        ok = resp.status_code == 200 and len(resp.text) > 100
        detail = f"HTTP {resp.status_code}" if ok else f"HTTP {resp.status_code}（内容异常）"
        return {"layer": "页面层", "ok": ok, "ms": ms, "detail": detail}
    except requests.exceptions.Timeout:
        return {"layer": "页面层", "ok": False, "ms": -1, "detail": "请求超时"}
    except Exception as e:
        return {"layer": "页面层", "ok": False, "ms": -1, "detail": str(e)}


def skip_layer(name, reason=""):
    return {"layer": name, "ok": True, "ms": -1, "detail": f"跳过{('（' + reason + '）') if reason else ''}"}


def skip_fail(name, reason=""):
    return {"layer": name, "ok": False, "ms": -1, "detail": f"跳过（{reason}）"}


# ── ERP 检测 ──────────────────────────────────────────────────

def check_erp():
    BASE = "https://erp.bjjcz.cn"
    layers = []

    r1 = check_tcp("erp.bjjcz.cn", 443)
    layers.append(r1)
    if not r1["ok"]:
        layers += [skip_fail("页面层", "连通失败"), skip_fail("登录层", "连通失败"), skip_fail("业务层", "连通失败")]
        return {"name": "ERP系统", "base_url": BASE, "layers": layers}

    layers.append(check_page(f"{BASE}/index.html"))

    # 登录
    start = time.monotonic()
    token = None
    try:
        resp = requests.post(
            f"{BASE}/login",
            json={"username": ERP_USERNAME, "password": ERP_PASSWORD},
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        ms = int((time.monotonic() - start) * 1000)
        body = resp.json()
        token = (body.get("data") or {}).get("adminToken")
        ok = body.get("code") == 0 and bool(token)
        detail = f"登录成功 HTTP {resp.status_code}" if ok else f"登录失败 code={body.get('code')} msg={body.get('msg')}"
        layers.append({"layer": "登录层", "ok": ok, "ms": ms, "detail": detail})
    except requests.exceptions.Timeout:
        layers.append({"layer": "登录层", "ok": False, "ms": -1, "detail": "登录超时"})
    except Exception as e:
        layers.append({"layer": "登录层", "ok": False, "ms": -1, "detail": str(e)})

    # 业务层
    if token:
        today = date.today().strftime("%Y-%m-%d")
        start = time.monotonic()
        try:
            resp = requests.get(
                f"{BASE}/examine/attendanceSign/getUserSignDetail/{today}",
                headers={"Admin-Token": token, "Content-Type": "application/json"},
                timeout=TIMEOUT,
            )
            ms = int((time.monotonic() - start) * 1000)
            body = resp.json()
            ok = body.get("code") == 0
            detail = f"考勤查询成功 HTTP {resp.status_code}" if ok else f"考勤查询失败 code={body.get('code')}"
            layers.append({"layer": "业务层", "ok": ok, "ms": ms, "detail": detail})
        except requests.exceptions.Timeout:
            layers.append({"layer": "业务层", "ok": False, "ms": -1, "detail": "请求超时"})
        except Exception as e:
            layers.append({"layer": "业务层", "ok": False, "ms": -1, "detail": str(e)})
        # 登出
        try:
            requests.post(f"{BASE}/loginOut", headers={"Admin-Token": token}, timeout=5)
        except Exception:
            pass
    else:
        layers.append(skip_fail("业务层", "登录失败无 token"))

    return {"name": "ERP系统", "base_url": BASE, "layers": layers}


# ── 激光云检测 ────────────────────────────────────────────────

def check_laser_cloud():
    BASE = "https://www.laser-cloud.cn"
    layers = []

    r1 = check_tcp("www.laser-cloud.cn", 443)
    layers.append(r1)
    if not r1["ok"]:
        layers += [skip_fail("页面层", "连通失败"), skip_fail("登录层", "连通失败")]
        return {"name": "激光云平台", "base_url": BASE, "layers": layers}

    layers.append(check_page(f"{BASE}/"))

    # 登录
    start = time.monotonic()
    try:
        resp = requests.post(
            f"{BASE}/public/login",
            json={
                "userName": LC_USERNAME,
                "password": LC_PASSWORD,
                "i18n": "zhCN",
                "lang": "zhCN",
            },
            headers={"Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        ms = int((time.monotonic() - start) * 1000)
        body = resp.json()
        token = (body.get("data") or {}).get("token")
        ok = body.get("code") == 0 and bool(token)
        detail = f"登录成功 HTTP {resp.status_code}" if ok else f"登录失败 code={body.get('code')} msg={body.get('msg')}"
        layers.append({"layer": "登录层", "ok": ok, "ms": ms, "detail": detail})
        # 暂无登出接口，token 自然过期
    except requests.exceptions.Timeout:
        layers.append({"layer": "登录层", "ok": False, "ms": -1, "detail": "登录超时"})
    except Exception as e:
        layers.append({"layer": "登录层", "ok": False, "ms": -1, "detail": str(e)})

    return {"name": "激光云平台", "base_url": BASE, "layers": layers}


# ── 判断状态 ──────────────────────────────────────────────────

def get_status(layers):
    """返回 ok / slow / error"""
    failed = [r for r in layers if not r["ok"] and "跳过" not in r["detail"]]
    if failed:
        return "error", failed
    slow = [r for r in layers if r["ok"] and r["ms"] > SLOW_MS]
    if slow:
        return "slow", slow
    return "ok", []


# ── 推送 ──────────────────────────────────────────────────────

def push_wecom(all_results, now_str):
    if not WECOM_WEBHOOK:
        return

    lines = []
    has_error = any(get_status(r["layers"])[0] == "error" for r in all_results)
    has_slow  = any(get_status(r["layers"])[0] == "slow"  for r in all_results)

    if not has_error and not has_slow:
        # 全部正常，绿色
        lines.append('<font color="info">✅ 全部服务正常</font>')
        lines.append(f'> 检测时间：{now_str}')
        lines.append("")
        for r in all_results:
            valid_ms = [l["ms"] for l in r["layers"] if l["ms"] > 0 and l["ok"]]
            avg = int(sum(valid_ms) / len(valid_ms)) if valid_ms else 0
            lines.append(f'> ✅ **{r["name"]}** 均{avg}ms')
    else:
        # 有问题，逐个展示
        if has_error:
            header_color = "warning"
            header_icon = "🔴"
            header_text = "服务异常"
        else:
            header_color = "comment"
            header_icon = "⚠️"
            header_text = "响应偏慢"

        problem_names = "、".join(
            r["name"] for r in all_results
            if get_status(r["layers"])[0] != "ok"
        )
        lines.append(f'<font color="{header_color}">{header_icon} {header_text} | {problem_names}</font>')
        lines.append(f'> 检测时间：{now_str}')
        lines.append("")

        for r in all_results:
            status, _ = get_status(r["layers"])
            status_icon = {"ok": "✅", "slow": "⚠️", "error": "🔴"}[status]
            lines.append(f'> {status_icon} **{r["name"]}**')
            for layer in r["layers"]:
                if "跳过" in layer["detail"]:
                    continue
                if not layer["ok"]:
                    icon = "❌"
                elif layer["ms"] > SLOW_MS and layer["ms"] > 0:
                    icon = "⚠️"
                else:
                    icon = "✅"
                ms_str = f'{layer["ms"]}ms' if layer["ms"] > 0 else "-"
                lines.append(f'>   {icon} {layer["layer"]}：{layer["detail"]} ({ms_str})')

    content = "\n".join(lines)
    try:
        resp = requests.post(
            WECOM_WEBHOOK,
            json={"msgtype": "markdown", "markdown": {"content": content}},
            timeout=10,
        )
        result = resp.json()
        print(f"[企业微信] {'成功' if result.get('errcode') == 0 else f'失败: {result}'}")
    except Exception as e:
        print(f"[企业微信] 推送失败: {e}")


def push_email(subject, content):
    if not all([NOTIFY_EMAIL, QQ_AUTH_CODE, EMAIL_SENDER]):
        return
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


# ── 主逻辑 ────────────────────────────────────────────────────

def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}\n检测时间: {now_str}\n{'='*50}")

    # 执行检测
    all_results = []
    for checker in [check_erp, check_laser_cloud]:
        result = checker()
        all_results.append(result)
        status, _ = get_status(result["layers"])
        print(f"\n[{result['name']}] 状态: {status}")
        for layer in result["layers"]:
            ms_str = f"{layer['ms']}ms" if layer["ms"] > 0 else "-"
            print(f"  {'✅' if layer['ok'] else '❌'} {layer['layer']}: {layer['detail']} ({ms_str})")

    # 推送企业微信（每次）
    push_wecom(all_results, now_str)

    # 邮件/Server酱 仅异常时推送（慢响应不发邮件，避免打扰）
    has_error = any(get_status(r["layers"])[0] == "error" for r in all_results)
    if has_error:
        lines = [f"检测时间: {now_str}", ""]
        for r in all_results:
            status, _ = get_status(r["layers"])
            lines.append(f"【{r['name']}】{'正常' if status == 'ok' else ('响应慢' if status == 'slow' else '异常')}")
            for layer in r["layers"]:
                if "跳过" not in layer["detail"]:
                    icon = "✅" if layer["ok"] else "❌"
                    ms_str = f"{layer['ms']}ms" if layer["ms"] > 0 else "-"
                    lines.append(f"  {icon} {layer['layer']}: {layer['detail']} ({ms_str})")
            lines.append("")
        error_names = "、".join(r["name"] for r in all_results if get_status(r["layers"])[0] == "error")
        title = f"🔴 服务告警 | {error_names} 异常"
        content = "\n".join(lines)
        push_email(title, content)
        push_serverchan(title, content)

    all_ok = not has_error
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
