"""
Скрипт для массовой регистрации аккаунтов на checko.ru
Временная почта: yopmail.com (через Playwright, без API ключа)

Зависимости:
    pip install playwright requests
    playwright install chromium
"""

import asyncio
import csv
import random
import string
import time
import re
import os

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ─── Настройки ────────────────────────────────────────────────────────────────
ACCOUNTS_COUNT  = int(os.environ.get("ACCOUNTS_COUNT", 30))
OUTPUT_CSV      = os.environ.get("OUTPUT_CSV", "checko_accounts.csv")
HEADLESS        = os.environ.get("HEADLESS", "True").lower() != "false"
DELAY_BETWEEN   = int(os.environ.get("DELAY_BETWEEN", 5))
CHECKO_REGISTER = "https://checko.ru/sign-up"
CHECKO_LOGIN    = "https://checko.ru/login"
CHECKO_API_PAGE = "https://checko.ru/user/account/api"
YOPMAIL_URL     = "https://yopmail.com/en/"
# ──────────────────────────────────────────────────────────────────────────────


def random_password(length: int = 14) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choices(chars, k=length))


def random_username(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=length))


# ─── Yopmail через Playwright ─────────────────────────────────────────────────

async def open_yopmail(browser, username: str):
    """
    Открывает yopmail.com с нужным адресом.
    Возвращает (mail_page, mail_context).
    email = username@yopmail.com
    """
    ctx  = await browser.new_context(user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ))
    page = await ctx.new_page()
    await page.goto(YOPMAIL_URL, wait_until="networkidle", timeout=30_000)
    await page.wait_for_timeout(1500)

    # Вводим имя ящика в поле
    inp = page.locator('input#login').first
    if not await inp.is_visible():
        inp = page.locator('input[name="login"]').first
    await inp.fill(username)

    btn = page.locator('button.md-but-primary, button[type="submit"]').first
    await btn.click()
    await page.wait_for_timeout(2000)

    return page, ctx


async def wait_for_confirmation_link_yopmail(mail_page, timeout: int = 120) -> str | None:
    """
    Обновляем yopmail и ищем письмо от checko.ru с ссылкой подтверждения.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # Нажимаем кнопку обновить inbox
            refresh = mail_page.locator('button#refresh, button.refresh, #refresh').first
            if await refresh.is_visible():
                await refresh.click()
            else:
                await mail_page.reload(wait_until="networkidle", timeout=15_000)
            await mail_page.wait_for_timeout(2000)

            # Ищем письма в iframe (yopmail использует iframe для inbox)
            frames = mail_page.frames
            for frame in frames:
                try:
                    body = await frame.inner_text("body")
                    links = re.findall(r'https?://checko\.ru[^\s"\'<>\)]+', body)
                    confirm_links = [
                        l for l in links
                        if any(kw in l for kw in (
                            "confirm", "verify", "activate", "token", "email", "user"
                        ))
                    ]
                    if not confirm_links:
                        confirm_links = [l for l in links
                                         if l.rstrip("/") != "https://checko.ru"]
                    if confirm_links:
                        return confirm_links[0]
                except Exception:
                    pass

            # Также проверяем основной контент страницы
            body = await mail_page.inner_text("body")
            links = re.findall(r'https?://checko\.ru[^\s"\'<>\)]+', body)
            confirm_links = [
                l for l in links
                if any(kw in l for kw in (
                    "confirm", "verify", "activate", "token", "email", "user"
                ))
            ]
            if confirm_links:
                return confirm_links[0]

        except Exception as e:
            print(f"  [~] Ошибка проверки почты: {e}")

        await asyncio.sleep(6)
    return None


# ─── Checko: регистрация ──────────────────────────────────────────────────────

async def register_on_checko(page, email: str, password: str) -> bool:
    try:
        await page.goto(CHECKO_REGISTER, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(1500)

        # Email
        await page.locator('input[type="email"]').first.fill(email)

        # Пароль + подтверждение
        pwd_fields = page.locator('input[type="password"]')
        count = await pwd_fields.count()
        for i in range(count):
            await pwd_fields.nth(i).fill(password)

        # Чекбокс согласия
        checkbox = page.locator('input[type="checkbox"]').first
        if await checkbox.is_visible() and not await checkbox.is_checked():
            await checkbox.check()

        await page.wait_for_timeout(500)

        # Кнопка "Зарегистрироваться"
        submit = page.locator('button:has-text("Зарегистрироваться")').first
        if not await submit.is_visible():
            submit = page.locator('button[type="submit"]').first
        await submit.click()
        await page.wait_for_timeout(3000)

        url_after = page.url
        print(f"  [~] URL после формы: {url_after}")

        # Скриншот первых 3 аккаунтов
        try:
            import glob
            existing = glob.glob("debug_reg_*.png")
            if len(existing) < 3:
                idx = len(existing) + 1
                await page.screenshot(path=f"debug_reg_{idx}.png", full_page=True)
                print(f"  [~] Скриншот регистрации: debug_reg_{idx}.png")
        except Exception:
            pass

        # Если остались на /sign-up — ошибка валидации
        if url_after.rstrip("/").endswith("sign-up"):
            body = await page.inner_text("body")
            # Ищем текст ошибки
            for line in body.splitlines():
                line = line.strip()
                if line and any(kw in line.lower() for kw in
                                ("недопустим", "некорректн", "уже зарегистр",
                                 "ошибка", "error", "invalid", "already", "занят")):
                    print(f"  [!] Ошибка формы: {line[:100]}")
            return False

        return True

    except PlaywrightTimeout as e:
        print(f"  [!] Timeout: {e}")
        return False
    except Exception as e:
        print(f"  [!] Ошибка регистрации: {e}")
        return False


async def login_on_checko(page, email: str, password: str) -> bool:
    try:
        if "/login" not in page.url and "new_session" not in page.url:
            return True  # уже залогинены

        await page.goto(CHECKO_LOGIN, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(1000)

        await page.locator('input[type="email"]').first.fill(email)
        await page.locator('input[type="password"]').first.fill(password)

        submit = page.locator('button:has-text("Войти")').first
        if not await submit.is_visible():
            submit = page.locator('button[type="submit"]').first
        await submit.click()
        await page.wait_for_timeout(3000)

        print(f"  [~] URL после логина: {page.url}")
        return True
    except Exception as e:
        print(f"  [!] Ошибка логина: {e}")
        return False


async def confirm_email_in_browser(page, confirm_url: str) -> bool:
    try:
        await page.goto(confirm_url, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(2000)
        print(f"  [~] URL после подтверждения: {page.url}")
        return True
    except Exception as e:
        print(f"  [!] Ошибка подтверждения: {e}")
        return False


async def get_api_key(page) -> str | None:
    try:
        await page.goto(CHECKO_API_PAGE, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(2000)
        print(f"  [~] URL страницы API: {page.url}")

        # Скриншот первых 3
        try:
            import glob
            existing = glob.glob("debug_api_*.png")
            if len(existing) < 3:
                idx = len(existing) + 1
                await page.screenshot(path=f"debug_api_{idx}.png", full_page=True)
                print(f"  [~] Скриншот API: debug_api_{idx}.png")
        except Exception:
            pass

        # Если редиректнуло на логин — не авторизованы
        if "/login" in page.url or "new_session" in page.url:
            print("  [!] Не авторизованы на странице API")
            return None

        # Ищем ключ в тексте страницы
        content = await page.inner_text("body")
        match = re.search(r'Ваш API ключ[:\s]*([A-Za-z0-9_\-]{16,})', content)
        if match:
            return match.group(1).strip()

        # Ищем в HTML (value, data-атрибуты)
        html = await page.content()
        match = re.search(r'Ваш API ключ.*?([A-Za-z0-9_\-]{20,})', html, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Ищем input или code с ключом
        for sel in ['input[name*="api"]', 'input[id*="api"]', 'code', 'pre', '.api-key']:
            el = page.locator(sel).first
            if await el.is_visible():
                val = (await el.input_value() if sel.startswith("input")
                       else await el.inner_text())
                val = val.strip()
                if len(val) > 16:
                    return val

        print("  [!] API ключ не найден на странице")
        return None
    except Exception as e:
        print(f"  [!] Ошибка получения API ключа: {e}")
        return None


# ─── Статус ───────────────────────────────────────────────────────────────────

def _set_running_flag(running: bool, total: int = 0):
    with open(".status", "w") as f:
        f.write(f"{running}|{total}")


# ─── Основной цикл ────────────────────────────────────────────────────────────

async def main():
    print(f"[*] Старт. Создаём {ACCOUNTS_COUNT} аккаунтов...")
    _set_running_flag(True, ACCOUNTS_COUNT)

    results = []
    csv_file   = open(OUTPUT_CSV, "w", newline="", encoding="utf-8")
    csv_writer = csv.DictWriter(csv_file, fieldnames=["login", "password", "api_key"],
                                delimiter="|")
    csv_writer.writeheader()
    csv_file.flush()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        for i in range(1, ACCOUNTS_COUNT + 1):
            print(f"\n[{i}/{ACCOUNTS_COUNT}] Создаём аккаунт...")

            mail_username = random_username(10)
            email         = f"{mail_username}@yopmail.com"
            password      = random_password()
            print(f"  [+] Email: {email}")

            # 1. Открываем yopmail заранее
            mail_page, mail_ctx = await open_yopmail(browser, mail_username)

            # 2. Регистрация на checko.ru в отдельном контексте
            checko_ctx  = await browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ))
            checko_page = await checko_ctx.new_page()

            success = await register_on_checko(checko_page, email, password)
            if not success:
                print("  [!] Регистрация не удалась, пропускаем")
                await checko_ctx.close()
                await mail_ctx.close()
                continue

            print("  [+] Форма отправлена, ждём письмо на yopmail...")

            # 3. Ждём письмо с подтверждением
            confirm_url = await wait_for_confirmation_link_yopmail(mail_page, timeout=120)
            await mail_ctx.close()

            if confirm_url:
                print(f"  [+] Ссылка: {confirm_url[:70]}...")
                await confirm_email_in_browser(checko_page, confirm_url)
                # После подтверждения может быть редирект на логин
                await login_on_checko(checko_page, email, password)
            else:
                print("  [~] Письмо не пришло — пробуем войти напрямую")
                await login_on_checko(checko_page, email, password)

            # 4. Получаем API ключ
            api_key = await get_api_key(checko_page)
            if api_key:
                print(f"  [+] API ключ: {api_key[:24]}...")
            else:
                api_key = "NOT_FOUND"

            results.append({"login": email, "password": password, "api_key": api_key})
            csv_writer.writerow({"login": email, "password": password, "api_key": api_key})
            csv_file.flush()
            print(f"  [+] Сохранено ({len(results)}/{ACCOUNTS_COUNT})")

            await checko_ctx.close()

            if i < ACCOUNTS_COUNT:
                print(f"  [~] Пауза {DELAY_BETWEEN}с...")
                await asyncio.sleep(DELAY_BETWEEN)

        await browser.close()

    csv_file.close()
    _set_running_flag(False)
    print(f"\n[✓] Готово! {len(results)} аккаунтов в {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
