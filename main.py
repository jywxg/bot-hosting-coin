import os
import asyncio
import re
import json
import traceback
import urllib.request
import urllib.error
from playwright.async_api import async_playwright

# ===== 配置区（已更新为 legacy 域名）=====
TARGET_URL = "https://legacy.bot-hosting.net/panel/"
EARN_URL = "https://legacy.bot-hosting.net/panel/earn"
PROXY_URL = os.getenv("PROXY")
TOKEN = os.getenv("TOKEN")

LOCALSTORAGE_ITEMS = {
    "token": TOKEN
}

ESSENTIAL_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "dnt": "1"
}

# ===== Telegram 通知模块 =====
def send_tg_notification_sync(message):
    """同步发送 TG 通知，使用内置库避免增加 requirements 负担"""
    bot_token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    
    if not bot_token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
        print("  📢 TG 通知发送成功")
    except Exception as e:
        print(f"  ⚠️ TG 通知发送失败: {e}")

async def send_tg_msg(message):
    """异步包装 TG 通知函数"""
    await asyncio.to_thread(send_tg_notification_sync, message)

# ===== 代理解析 =====
def parse_proxy(proxy_url):
    if not proxy_url:
        return None
    proxy_url = proxy_url.rstrip('/')
    try:
        if "://" not in proxy_url:
            proxy_url = "http://" + proxy_url
        protocol, rest = proxy_url.split("://", 1)
        if "@" in rest:
            auth, host_port = rest.split("@", 1)
            username, password = auth.split(":", 1)
        else:
            username = password = None
            host_port = rest
        proxy_config = {"server": f"{protocol}://{host_port}"}
        if username and password:
            proxy_config["username"] = username
            proxy_config["password"] = password
        return proxy_config
    except Exception as e:
        print(f"⚠️  代理解析失败: {e}，将不使用代理")
        return None

# ===== Cookie 解析（兼容 legacy 域名）=====
def parse_cookies(cookie_str, domain="legacy.bot-hosting.net", path="/"):
    cookies = []
    for item in cookie_str.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": domain,
            "path": path
        })
    return cookies

# ===== hCaptcha 处理 =====
async def solve_hcaptcha(page):
    try:
        from hcaptcha_challenger.agent import AgentV, AgentConfig
        from hcaptcha_challenger.models import CaptchaResponse
        
        CONFIGURED_MODEL = "gemini-2.5-flash-image"
        print(f"🛡️  【配置模型】{CONFIGURED_MODEL}")
        
        agent_config = AgentConfig(model=CONFIGURED_MODEL)
        agent = AgentV(page=page, agent_config=agent_config)
        
        print("  → 点击 hcaptcha 复选框...")
        await agent.robotic_arm.click_checkbox()
        
        print("  → 等待挑战加载并自动解决...")
        await agent.wait_for_challenge()
        
        if agent.cr_list:
            cr: CaptchaResponse = agent.cr_list[-1]
            print("  ✓ hCaptcha 验证成功！")
            return True
        else:
            print("  ℹ️  验证完成（无挑战响应）")
            return True
            
    except ImportError:
        print("⚠️  hcaptcha_challenger 未安装，跳过验证")
        return False
    except Exception as e:
        print(f"⚠️  hCaptcha 处理出错: {e}")
        return False

# ===== 强制关闭所有弹窗 =====
async def force_close_all_modals(page):
    closed_any = False
    print("  → 强制清理所有弹窗...")
    
    try:
        ok_button = await page.wait_for_selector('button.swal-button.swal-button--confirm', timeout=2000)
        if ok_button and await ok_button.is_visible():
            await ok_button.click()
            closed_any = True
            await page.wait_for_timeout(2000)
    except:
        pass
    
    try:
        selectors = ['div.modal-content span.close', 'span.close', '.modal-content .close']
        for selector in selectors:
            close_button = await page.query_selector(selector)
            if close_button and await close_button.is_visible():
                await close_button.click()
                closed_any = True
                await page.wait_for_timeout(2000)
                break
    except:
        pass
    
    return closed_any

# ===== 智能关闭弹窗（带进度解析）=====
async def close_all_modals(page):
    claimed, total = None, None
    try:
        print("  → 等待成功弹窗出现...")
        await page.wait_for_selector('.swal-modal', timeout=15000)
        await page.wait_for_timeout(1500)
        
        try:
            text_content = await page.locator('.swal-text').inner_text()
            match = re.search(r'(\d+)\s*/\s*(\d+)', text_content)
            if match:
                claimed = int(match.group(1))
                total = int(match.group(2))
                print(f"  📊 进度更新: {claimed}/{total}")
        except:
            pass
        
        ok_button = await page.wait_for_selector('button.swal-button.swal-button--confirm', timeout=5000)
        if ok_button:
            await ok_button.click()
            await page.wait_for_timeout(2000)
        
        try:
            await page.wait_for_selector('.swal-modal', state='hidden', timeout=10000)
        except:
            pass
        
        try:
            selectors = ['div.modal-content span.close', 'span.close', '.modal-content .close']
            for selector in selectors:
                close_button = await page.query_selector(selector)
                if close_button and await close_button.is_visible():
                    await close_button.click()
                    await page.wait_for_timeout(2000)
                    break
        except:
            pass
        
        await page.wait_for_timeout(2000)
        return claimed, total
        
    except Exception as e:
        print(f"  ⚠️  处理弹窗失败: {e}")
        return None, None

# ===== 检查按钮状态并处理 hCaptcha =====
async def check_button_and_solve_hcaptcha(page, max_retries=3):
    claim_button_selector = 'button.btn.green[type="submit"]'
    for retry in range(max_retries):
        try:
            claim_button = await page.wait_for_selector(claim_button_selector, timeout=10000)
            if not claim_button:
                return False
            
            is_disabled = await claim_button.is_disabled()
            button_text = await claim_button.inner_text()
            
            if not is_disabled:
                return True
            
            if "complete the captcha" in button_text.lower():
                success = await solve_hcaptcha(page)
                if success:
                    await page.wait_for_timeout(3000)
                    claim_button = await page.query_selector(claim_button_selector)
                    if claim_button and not await claim_button.is_disabled():
                        return True
                return False
            elif "you are on cooldown" in button_text.lower():
                return False
            else:
                return False
        except:
            return False
    return False

# ===== 点击领取按钮 =====
async def click_claim_coins(page, max_attempts=15):
    claim_button_selector = 'button.btn.green[type="submit"]'
    total_coins = 10
    claimed_so_far = 0
    task_completed = False
    
    for attempt in range(1, max_attempts + 1):
        if task_completed:
            break
        
        print(f"\n【尝试 {attempt}/{max_attempts} | 已领: {claimed_so_far}/{total_coins}】")
        await force_close_all_modals(page)
        button_ready = await check_button_and_solve_hcaptcha(page, max_retries=3)
        
        if not button_ready:
            try:
                claim_button = await page.query_selector(claim_button_selector)
                if claim_button:
                    button_text = await claim_button.inner_text()
                    if "you are on cooldown" in button_text.lower():
                        await page.wait_for_timeout(35000)
                        continue
            except:
                pass
            await page.wait_for_timeout(8000)
            continue
        
        claim_button = await page.wait_for_selector(claim_button_selector, timeout=15000)
        if not claim_button or await claim_button.is_disabled():
            await page.wait_for_timeout(8000)
            continue
        
        print("  → 点击领取按钮...")
        await claim_button.click()
        await page.wait_for_timeout(18000)
        
        claimed, total = await close_all_modals(page)
        
        if claimed is not None and total is not None:
            claimed_so_far = claimed
            total_coins = total
            if claimed >= total:
                task_completed = True
        
        wait_time = 1 if task_completed else 10
        await page.wait_for_timeout(wait_time * 1000)
    
    return task_completed, claimed_so_far, total_coins

# ===== 主流程 =====
async def main():
    if not TOKEN:
        err_msg = "❌ 环境变量 'TOKEN' 未设置！任务终止。"
        print(err_msg)
        await send_tg_msg(f"<b>Bot-Hosting 领币任务失败</b>\n\n{err_msg}")
        return

    await send_tg_msg("🚀 <b>Bot-Hosting 领币任务已启动 (Legacy)</b>")

    proxy_config = parse_proxy(PROXY_URL)
    browser = None
    
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                proxy=proxy_config
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 Edg/144.0.0.0",
                locale="zh-CN",
                timezone_id="Asia/Shanghai"
            )
            
            page = await context.new_page()
            
            async def intercept_route(route):
                if route.request.resource_type == "document":
                    await route.continue_(headers=ESSENTIAL_HEADERS)
                else:
                    await route.continue_()
            
            await page.route("**/*", intercept_route)
            
            print(f"\n→ 访问 {TARGET_URL}")
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            
            for key, value in LOCALSTORAGE_ITEMS.items():
                await page.evaluate(f"localStorage.setItem('{key}', '{value}')")
            
            print(f"→ 跳转到 {EARN_URL}")
            await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=60000)
            
            success, claimed, total = await click_claim_coins(page, max_attempts=15)
            
            # ===== 结果统计与通知 =====
            if success or (claimed >= total):
                success_msg = f"✅ <b>领取任务全部完成！</b>\n\n📊 最终进度: {claimed}/{total}"
                print(f"\n{success_msg}")
                await send_tg_msg(success_msg)
            else:
                warn_msg = f"⚠️ <b>领取任务未完全达标</b>\n\n📊 当前进度: {claimed}/{total}\n⏳ 可能是次数用尽或网页异常。"
                print(f"\n{warn_msg}")
                await send_tg_msg(warn_msg)
            
            await page.wait_for_timeout(10000)
            
        except Exception as e:
            err_details = traceback.format_exc()
            print(f"\n❌ 运行时发生严重错误:\n{err_details}")
            await send_tg_msg(f"❌ <b>Bot-Hosting 领币任务崩溃</b>\n\n<pre>{e}</pre>")
        finally:
            if browser:
                print("🔒 正在清理并关闭浏览器资源...")
                await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
