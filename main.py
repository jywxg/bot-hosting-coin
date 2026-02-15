import os
import asyncio
import re
import json
from playwright.async_api import async_playwright

# ===== 配置区（精简必要参数）=====
TARGET_URL = "https://bot-hosting.net/panel/"  # 已移除末尾空格
EARN_URL = "https://bot-hosting.net/panel/earn"  # 已移除末尾空格

# COOKIES_STR = os.getenv("COOKIES")

# 从环境变量读取代理配置
PROXY_URL = os.getenv("PROXY")

# localStorage 配置
LOCALSTORAGE_ITEMS = {
    "token": os.getenv("TOKEN")
}

# 仅保留浏览器通常不会自动设置/需显式覆盖的关键头
ESSENTIAL_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "dnt": "1"
}

# ===== Cookie 解析（保留必要属性）=====
def parse_cookies(cookie_str, domain="bot-hosting.net", path="/"):
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
            "path": path,
            "secure": True,
            "httpOnly": False
        })
    return cookies

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

# ===== hCaptcha 处理（带模型使用确认）=====
async def solve_hcaptcha(page):
    """
    尝试解决页面上的 hCaptcha 验证
    返回: True (成功) / False (失败或超时)
    """
    try:
        from hcaptcha_challenger.agent import AgentV, AgentConfig
        from hcaptcha_challenger.models import CaptchaResponse
        
        # 配置指定的AI模型（这是唯一能确认的点）
        CONFIGURED_MODEL = "gemini-2.5-flash-image"
        print(f"🛡️  【配置模型】{CONFIGURED_MODEL}")
        
        # 创建 agent 时明确指定模型
        agent_config = AgentConfig(model=CONFIGURED_MODEL)
        agent = AgentV(page=page, agent_config=agent_config)
        
        # 点击 hcaptcha 复选框
        print("  → 点击 hcaptcha 复选框...")
        await agent.robotic_arm.click_checkbox()
        
        # 等待挑战出现并自动解决
        print("  → 等待挑战加载并自动解决...")
        await agent.wait_for_challenge()
        
        # 验证结果处理（关键：无法获取实际模型，但可确认配置生效）
        if agent.cr_list:
            cr: CaptchaResponse = agent.cr_list[-1]
            
            # 【重要说明】库设计限制：CaptchaResponse 不包含模型字段
            # 我们只能确认：1) 挑战成功 2) 使用了配置的模型（通过库行为推断）
            print("  ✓ hCaptcha 验证成功！")
            
            # 提取关键验证信息（虽无模型，但有其他有用信息）
            response_data = cr.model_dump(by_alias=True)
            
            # 尝试提取任何可能的模型线索（防御性编程）
            model_hint = "未在响应中找到模型字段"
            for key in ['model', 'label', 'ai_model', 'provider', 'engine', 'solver']:
                if key in response_data and response_data[key]:
                    model_hint = f"{key}={response_data[key]}"
                    break
            
            # 关键日志：明确说明限制 + 确认配置
            print(f"  ℹ️  模型使用说明:")
            print(f"     • 配置模型: {CONFIGURED_MODEL}")
            # 打印关键响应字段（便于人工验证）
            print(f"    响应关键字段:")
            print(f"      - success: {response_data.get('success', 'N/A')}")
            print(f"      - generated_pass_UUID: {response_data.get('generated_pass_UUID', 'N/A')[:20]}...")
            print(f"      - challenge_score: {response_data.get('challenge_score', 'N/A')}")

            return True
        else:
            print("  ℹ️  验证完成（无挑战响应）")
            return True
            
    except ImportError:
        print("⚠️  hcaptcha_challenger 库未安装，跳过自动验证")
        print("    请安装: pip install hcaptcha_challenger")
        return False
    except Exception as e:
        print(f"⚠️  hCaptcha 处理出错: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

# ===== 强制关闭所有弹窗 =====
async def force_close_all_modals(page):
    closed_any = False
    print("  → 强制清理所有弹窗...")
    
    # 1. 关闭 SweetAlert 成功弹窗
    try:
        ok_button = await page.wait_for_selector('button.swal-button.swal-button--confirm', timeout=2000)
        if ok_button and await ok_button.is_visible():
            print("  ✓ 检测到 SweetAlert 弹窗")
            await ok_button.click()
            print("  ✓ 已点击 OK 按钮")
            closed_any = True
            await page.wait_for_timeout(2000)
    except:
        pass
    
    # 2. 关闭广告弹窗
    try:
        selectors = ['div.modal-content span.close', 'span.close', '.modal-content .close']
        for selector in selectors:
            close_button = await page.query_selector(selector)
            if close_button and await close_button.is_visible():
                print(f"  ✓ 检测到广告弹窗（选择器: {selector}）")
                await close_button.click()
                print("  ✓ 已点击关闭按钮")
                closed_any = True
                await page.wait_for_timeout(2000)
                break
    except:
        pass
    
    # 3. 检查残留弹窗
    try:
        modal_elements = await page.query_selector_all('.swal-modal, .modal-content, [role="dialog"]')
        visible_modals = [el for el in modal_elements if await el.is_visible()]
        if visible_modals:
            print(f"  ⚠️  仍检测到 {len(visible_modals)} 个可见弹窗")
        else:
            if closed_any:
                print("  ✓ 所有弹窗已清理完毕")
            else:
                print("  ℹ️  未检测到任何弹窗")
    except:
        pass
    
    return closed_any

# ===== 智能关闭弹窗（带进度解析）=====
async def close_all_modals(page):
    claimed, total = None, None
    try:
        # 步骤 1: 等待并处理成功弹窗
        print("  → 等待成功弹窗出现...")
        await page.wait_for_selector('.swal-modal', timeout=15000)
        print("  ✓ 成功弹窗已出现")
        await page.wait_for_timeout(1500)
        
        # 获取弹窗文本并解析进度
        try:
            title = await page.locator('.swal-title').inner_text()
            text_content = await page.locator('.swal-text').inner_text()
            print(f"  弹窗内容: {title}")
            print(f"  {text_content}")
            
            # 精确解析进度（支持多种格式）
            match = re.search(r'(\d+)\s*/\s*(\d+)', text_content)
            if match:
                claimed = int(match.group(1))
                total = int(match.group(2))
                print(f"  📊 进度更新: {claimed}/{total}")
            else:
                print("  ⚠️  无法解析进度信息")
        except Exception as e:
            print(f"  ⚠️  无法解析弹窗文本: {e}")
        
        # 点击 OK 按钮
        print("  → 点击 OK 按钮...")
        ok_button = await page.wait_for_selector('button.swal-button.swal-button--confirm', timeout=5000)
        if ok_button:
            await ok_button.click()
            print("  ✓ OK 按钮已点击")
            await page.wait_for_timeout(2000)
        else:
            print("  ⚠️  未找到 OK 按钮")
        
        # 步骤 2: 等待成功弹窗消失
        print("  → 等待成功弹窗消失（最多 10 秒）...")
        try:
            await page.wait_for_selector('.swal-modal', state='hidden', timeout=10000)
            print("  ✓ 成功弹窗已消失")
        except:
            print("  ℹ️  成功弹窗已隐藏或不存在")
        
        # 步骤 3: 检查并关闭广告弹窗
        print("  → 检查广告弹窗...")
        try:
            selectors = ['div.modal-content span.close', 'span.close', '.modal-content .close']
            close_button = None
            for selector in selectors:
                close_button = await page.query_selector(selector)
                if close_button and await close_button.is_visible():
                    print(f"  ✓ 检测到广告弹窗（选择器: {selector}）")
                    break
            
            if close_button and await close_button.is_visible() and not await close_button.is_disabled():
                print("  → 点击广告弹窗关闭按钮...")
                await close_button.click()
                print("  ✓ 广告弹窗已关闭")
                await page.wait_for_timeout(2000)
            else:
                print("  ℹ️  未检测到广告弹窗")
        except Exception as e:
            print(f"  ℹ️  未检测到广告弹窗: {e}")
        
        # 步骤 4: 最终稳定等待
        print("  → 等待页面稳定（2秒）...")
        await page.wait_for_timeout(2000)
        
        return claimed, total
        
    except Exception as e:
        print(f"  ⚠️  处理弹窗失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return None, None

# ===== 检查按钮状态并处理 hCaptcha =====
async def check_button_and_solve_hcaptcha(page, max_retries=3):
    claim_button_selector = 'button.btn.green[type="submit"]'
    for retry in range(max_retries):
        try:
            print(f"  → 检查按钮状态 ({retry + 1}/{max_retries})...")
            claim_button = await page.wait_for_selector(claim_button_selector, timeout=10000)
            if not claim_button:
                print("  ✗ 未找到按钮")
                return False
            
            is_disabled = await claim_button.is_disabled()
            button_text = await claim_button.inner_text()
            print(f"  按钮状态: {'disabled' if is_disabled else 'enabled'}")
            print(f"  按钮文本: '{button_text}'")
            
            if not is_disabled:
                print("  ✓ 按钮已可用，无需 hCaptcha 验证")
                return True
            
            if "complete the captcha" in button_text.lower():
                print("  ⚠️  按钮处于 disabled 状态，需要 hCaptcha 验证")
                success = await solve_hcaptcha(page)
                if success:
                    print("  ✓ hCaptcha 验证成功")
                    await page.wait_for_timeout(3000)
                    claim_button = await page.query_selector(claim_button_selector)
                    if claim_button and not await claim_button.is_disabled():
                        print("  ✓ 按钮已变为可用状态")
                        return True
                    else:
                        print("  ℹ️  按钮仍为 disabled 状态，继续重试...")
                else:
                    print("  ⚠️  hCaptcha 验证失败")
                    return False
            elif "you are on cooldown" in button_text.lower():
                print("  ⚠️  按钮处于冷却状态，需要等待")
                return False
            else:
                print("  ℹ️  按钮处于 disabled 状态（其他原因）")
                return False
        except Exception as e:
            print(f"  ⚠️  检查按钮状态失败: {e}")
            return False
    
    print(f"  ⚠️  已达到最大重试次数 ({max_retries})，按钮仍为 disabled 状态")
    return False

# ===== 点击领取按钮（智能动态调整）=====
async def click_claim_coins(page, max_attempts=15):
    """
    完整的领取流程，智能动态调整尝试次数
    关键逻辑：完成当前循环所有步骤后，根据进度决定是否继续下一次循环
    """
    print(f"\n🎯 开始领取流程（最多尝试 {max_attempts} 次）...")
    
    claim_button_selector = 'button.btn.green[type="submit"]'
    total_coins = 10  # 默认每日上限
    claimed_so_far = 0
    task_completed = False  # 标记任务是否已完成
    
    for attempt in range(1, max_attempts + 1):
        # ===== 循环开始：检查是否已达成目标 =====
        if task_completed:
            print(f"\n{'='*50}")
            print(f"✅ 任务已在上一轮完成（进度: {claimed_so_far}/{total_coins}），不再进行本次尝试")
            print(f"{'='*50}")
            break
        
        remaining_needed = total_coins - claimed_so_far
        print(f"\n{'='*50}")
        print(f"【尝试 {attempt}/{max_attempts} | 剩余需领取: {max(0, remaining_needed)}】")
        print(f"{'='*50}")
        
        # 清理残留弹窗
        print("  → 清理可能存在的残留弹窗...")
        await force_close_all_modals(page)
        print("  ✓ 弹窗清理完成")
        
        # 检查按钮状态
        print("  → 检查按钮状态并处理 hCaptcha（如需要）...")
        button_ready = await check_button_and_solve_hcaptcha(page, max_retries=3)
        
        if not button_ready:
            try:
                claim_button = await page.query_selector(claim_button_selector)
                if claim_button:
                    button_text = await claim_button.inner_text()
                    if "you are on cooldown" in button_text.lower():
                        cooldown_wait = 35
                        print(f"  → 检测到冷却状态，等待 {cooldown_wait} 秒...")
                        await page.wait_for_timeout(cooldown_wait * 1000)
                        print(f"  ✓ 已等待 {cooldown_wait} 秒")
                        continue
            except:
                pass
            await page.wait_for_timeout(8000)
            continue
        
        # 点击按钮
        print("  → 等待领取按钮出现...")
        claim_button = await page.wait_for_selector(claim_button_selector, timeout=15000)
        if not claim_button or await claim_button.is_disabled():
            print("  ⚠️  按钮不可用，跳过本次尝试")
            await page.wait_for_timeout(8000)
            continue
        
        button_text = await claim_button.inner_text()
        print(f"  找到按钮: '{button_text}'")
        print("  → 点击领取按钮...")
        await claim_button.click()
        print("  ✓ 按钮已点击")
        
        # 等待弹窗出现
        wait_for_modal = 18
        print(f"  → 等待 {wait_for_modal} 秒，确保弹窗出现...")
        await page.wait_for_timeout(wait_for_modal * 1000)
        print(f"  ✓ 已等待 {wait_for_modal} 秒")
        
        # 处理弹窗并获取进度（完成当前循环的核心步骤）
        print("  → 处理所有弹窗（成功弹窗 + 广告弹窗）...")
        claimed, total = await close_all_modals(page)
        
        # ===== 循环结束：更新进度并判断是否继续 =====
        # 更新全局进度
        if claimed is not None and total is not None:
            claimed_so_far = claimed
            total_coins = total
            remaining = total - claimed
            print(f"  📊 本轮后进度: {claimed}/{total} (剩余 {remaining} 次)")
            
            # 关键判断：如果已达成目标，标记任务完成（将在下一轮循环开始时退出）
            if claimed >= total:
                print(f"\n🎉 本轮已达成全部领取目标！({claimed}/{total})")
                print("   本次循环所有步骤已完成，将在下一轮开始时终止任务")
                task_completed = True  # 标记任务完成，但不中断当前循环
            elif remaining <= 0:
                print(f"\n🎉 已完成全部领取目标！")
                task_completed = True
        else:
            print("  ⚠️  无法获取进度信息")
        
        # 动态调整等待时间
        if task_completed is True:
            wait_time = 1
        else:
            wait_time = 10
        print(f"  → 等待 {wait_time} 秒...")
        await page.wait_for_timeout(wait_time * 1000)
        print(f"  ✓ 已等待 {wait_time} 秒，准备下一次尝试")
    
    # 最终结果汇报
    if task_completed or (claimed_so_far >= total_coins):
        print(f"\n✅ 任务成功完成！最终进度: {claimed_so_far}/{total_coins}")
        return True
    else:
        print(f"\n⚠️  未达到目标进度（当前: {claimed_so_far}/{total_coins}），已用完所有尝试次数")
        return False

# ===== 主流程（异步版本）=====
async def main():
    proxy_config = parse_proxy(PROXY_URL)
    if proxy_config:
        print(f"✓ 使用代理: {proxy_config['server']}")
    else:
        print("ℹ️  未检测到代理配置（环境变量 PROXY 未设置或格式错误）")
    
    async with async_playwright() as p:
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
        
        # await context.add_cookies(parse_cookies(COOKIES_STR))
        page = await context.new_page()
        
        async def intercept_route(route):
            if route.request.resource_type == "document":
                await route.continue_(headers=ESSENTIAL_HEADERS)
            else:
                await route.continue_()
        
        await page.route("**/*", intercept_route)
        
        # 步骤 1-3: 访问页面、写入 localStorage、跳转
        print(f"\n→ 步骤 1: 访问 {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
        print("✓ 首次页面加载完成")
        
        print("\n→ 步骤 2: 写入 localStorage")
        for key, value in LOCALSTORAGE_ITEMS.items():
            await page.evaluate(f"localStorage.setItem('{key}', '{value}')")
            print(f"  ✓ 已写入: {key}")
        
        print(f"\n→ 步骤 3: 跳转到 {EARN_URL}")
        await page.goto(EARN_URL, wait_until="domcontentloaded", timeout=60000)
        print("✓ 跳转完成")
        
        # 步骤 4: 初始按钮状态检查
        print("\n→ 步骤 4: 检查初始按钮状态")
        await check_button_and_solve_hcaptcha(page, max_retries=2)
        
        # 步骤 5: 开始领取流程
        print("\n→ 步骤 5: 开始自动领取（智能进度跟踪）")
        success = await click_claim_coins(page, max_attempts=15)
        
        if success:
            print("\n✅ 领取任务全部完成！")
        else:
            print("\n⚠️  领取任务未完成，请检查页面状态")
        
        # 步骤 6: 等待
        print("\n→ 步骤 6: 保持页面打开 30 秒...")
        await page.wait_for_timeout(30000)
        print("✅ 任务完成，关闭浏览器")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())