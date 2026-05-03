"""
Скрипт для массовой регистрации аккаунтов на checko.ru
Временная почта: mail.tm (бесплатный API, без ключа)

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

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ─── Настройки ────────────────────────────────────────────────────────────────
ACCOUNTS_COUNT  = int(os.environ.get("ACCOUNTS_COUNT", 30))
OUTPUT_CSV      = os.environ.get("OUTPUT_CSV", "checko_accounts.csv")
HEADLESS        = os.environ.get("HEADLESS", "True").lower() != "false"
DELAY_BETWEEN   = int(os.environ.get("DELAY_BETWEEN", 5))
CHECKO_REGISTER = "https://checko.ru/sign-up"
CHECKO_LOGIN    = "https://checko.ru/login"
CHECKO_API_PAGE = "https://checko.ru/user/account/api"
MAIL_TM_BASE    = "https://api.mail.tm"
# ──────────────────────────────────────────────────────────────────────────────


def random_password(length: int = 14) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choices(chars, k=length))


def random_username(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


# ─── mail.tm API ──────────────────────────────────────────────────────────────

def get_mailtm_domain() -> str:
    r = requests.get(f"{MAIL_TM_BASE}/domains", timeout=15)
    r.raise_for_status()
    domains = r.json().get("hydra:member", [])
    if not domains:
        raise RuntimeError("mail.tm не вернул ни одного домена")
    return domains[0]["domain"]


def create_mailtm_account(address: str, password: str) -> dict:
    r = requests.post(
        f"{MAIL_TM_BASE}/accounts",
        json={"address": address, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_mailtm_token(address: str, password: str) -> str:
    r = requests.post(
        f"{MAIL_TM_BASE}/token",
        json={"address": address, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def wait_for_confirmation_link(token: str, timeout: int = 60) -> str | None:
    """Ждём письмо от checko.ru и возвращаем ссылку подтверждения."""
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout
    seen_ids = set()

    while time.time() < deadline:
        try:
            r = requests.get(f"{MAIL_TM_BASE}/messages", headers=headers, timeout=15)
            if r.status_code != 200:
                time.sleep(3)
                continue
            messages = r.json().get("hydra:member", [])
            for msg in messages:
                msg_id = msg["id"]
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)

                detail = requests.get(
                    f"{MAIL_TM_BASE}/messages/{msg_id}",
                    headers=headers, timeout=15,
                ).json()

                body = (detail.get("text", "") or "") + (detail.get("html", "") or "")
                links = re.findall(r'https?://checko\.ru[^\s"\'<>\)]+', body)
                # Фильтруем ссылки подтверждения
                confirm = [l for l in links
                           if any(kw in l for kw in
                                  ("confirm", "verify", "activate", "token",
                                   "email", "user"))]
                if not confirm:
                    confirm = [l for l in links if l.rstrip("/") != "https://checko.ru"]
                if confirm:
                    return confirm[0]
        except Exception as e:
            print(f"  [~] Ошибка проверки почты: {e}")
        time.sleep(3)
    return None


# ─── Checko: регистрация ──────────────────────────────────────────────────────

async def register_on_checko(page, email: str, password: str) -> bool:
    try:
        await page.goto(CHECKO_REGISTER, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(2000)

        # Закрываем cookie-баннер если есть
        try:
            cookie_btn = page.locator('button:has-text("×"), .cookie-close, [aria-label="Close"]').first
            if await cookie_btn.is_visible(timeout=1000):
                await cookie_btn.click()
                await page.wait_for_timeout(500)
        except Exception:
            pass

        # Скриншот ДО заполнения — видим форму
        try:
            import glob
            existing = glob.glob("debug_before_*.png")
            if len(existing) < 2:
                idx = len(existing) + 1
                await page.screenshot(path=f"debug_before_{idx}.png", full_page=True)
                print(f"  [~] Скриншот до заполнения: debug_before_{idx}.png")
        except Exception:
            pass

        # Email — пробуем несколько селекторов
        email_filled = False
        for sel in ['input[type="email"]', 'input[name="email"]', 'input[name="user[email]"]',
                     'input[placeholder*="mail" i]', 'input[placeholder*="почт" i]', '#email']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=1000):
                    await el.fill(email)
                    email_filled = True
                    print(f"  [~] Email заполнен через: {sel}")
                    break
            except Exception:
                continue
        if not email_filled:
            print("  [!] Не нашёл поле email!")
            return False

        # Пароль + подтверждение
        pwd_fields = page.locator('input[type="password"]')
        count = await pwd_fields.count()
        print(f"  [~] Найдено полей пароля: {count}")
        for i in range(count):
            await pwd_fields.nth(i).fill(password)

        # Чекбокс согласия
        try:
            checkbox = page.locator('input[type="checkbox"]').first
            if await checkbox.is_visible(timeout=1000):
                if not await checkbox.is_checked():
                    await checkbox.check(force=True)
                    print("  [~] Чекбокс отмечен")
        except Exception:
            print("  [~] Чекбокс не найден или уже отмечен")

        await page.wait_for_timeout(500)

        # Кнопка отправки
        submitted = False
        for sel in ['button:has-text("Зарегистрироваться")', 'input[type="submit"]',
                     'button[type="submit"]', 'button:has-text("Регистрация")']:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    submitted = True
                    print(f"  [~] Кнопка нажата: {sel}")
                    break
            except Exception:
                continue
        if not submitted:
            print("  [!] Не нашёл кнопку отправки!")

        # Ждём навигацию или ответ
        await page.wait_for_timeout(4000)

        url_after = page.url
        print(f"  [~] URL после формы: {url_after}")

        # Скриншот ПОСЛЕ отправки
        try:
            import glob
            existing = glob.glob("debug_reg_*.png")
            if len(existing) < 3:
                idx = len(existing) + 1
                await page.screenshot(path=f"debug_reg_{idx}.png", full_page=True)
                print(f"  [~] Скриншот регистрации: debug_reg_{idx}.png")
        except Exception:
            pass

        # Проверяем ошибки
        if url_after.rstrip("/").endswith("sign-up"):
            body = await page.inner_text("body")
            for line in body.splitlines():
                line = line.strip()
                if line and any(kw in line.lower() for kw in
                                ("недопустим", "некорректн", "уже зарегистр",
                                 "ошибка", "error", "invalid", "already", "занят")):
                    print(f"  [!] Ошибка формы: {line[:120]}")
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

        if "/login" in page.url or "new_session" in page.url:
            print("  [!] Не авторизованы — редирект на логин")
            return None

        # Скриншот
        try:
            import glob
            existing = glob.glob("debug_api_*.png")
            if len(existing) < 3:
                idx = len(existing) + 1
                await page.screenshot(path=f"debug_api_{idx}.png", full_page=True)
                print(f"  [~] Скриншот API: debug_api_{idx}.png")
        except Exception:
            pass

        # Ищем ключ в тексте
        content = await page.inner_text("body")
        print(f"  [~] Текст страницы API (первые 300 символов): {content[:300]}")

        match = re.search(r'(?:Ваш API ключ|API key|api.key)[:\s]*([A-Za-z0-9_\-]{10,})', content, re.IGNORECASE)
        if match:
            return match.group(1).strip()

        # Ищем в HTML
        html = await page.content()
        match = re.search(r'(?:api.key|api_key|apikey)["\s:=]+([A-Za-z0-9_\-]{16,})', html, re.IGNORECASE)
        if match:
            return match.group(1).strip()

        # Ищем input/code
        for sel in ['input[name*="api"]', 'input[id*="api"]', 'code', 'pre', '.api-key']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=1000):
                    val = (await el.input_value() if sel.startswith("input")
                           else await el.inner_text())
                    val = val.strip()
                    if len(val) > 10:
                        return val
            except Exception:
                continue

        print("  [!] API ключ не найден")
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

    # Получаем домен mail.tm
    domain = get_mailtm_domain()
    print(f"[*] Домен mail.tm: @{domain}")

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
            print(f"\n[{i}/{ACCOUNTS_COUNT}] ─────────────────────────────")

            username = random_username(10)
            email    = f"{username}@{domain}"
            password = random_password()

            # 1. Создать email на mail.tm
            try:
                create_mailtm_account(email, password)
                token = get_mailtm_token(email, password)
                print(f"  [+] Email создан: {email}")
            except Exception as e:
                print(f"  [!] Ошибка mail.tm: {e}")
                continue

            # 2. Регистрация на checko.ru
            ctx  = await browser.new_context(user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ))
            page = await ctx.new_page()

            ok = await register_on_checko(page, email, password)
            if not ok:
                print("  [!] Регистрация не удалась")
                await ctx.close()
                continue

            print("  [+] Форма отправлена, ждём письмо (до 60с)...")

            # 3. Ждём подтверждение email
            confirm_url = wait_for_confirmation_link(token, timeout=60)
            if confirm_url:
                print(f"  [+] Ссылка: {confirm_url[:70]}...")
                await confirm_email_in_browser(page, confirm_url)
                await login_on_checko(page, email, password)
            else:
                print("  [~] Письмо не пришло — пробуем войти")
                await login_on_checko(page, email, password)

            # 4. API ключ
            api_key = await get_api_key(page)
            if api_key:
                print(f"  [+] API ключ: {api_key[:24]}...")
            else:
                api_key = "NOT_FOUND"

            results.append({"login": email, "password": password, "api_key": api_key})
            csv_writer.writerow({"login": email, "password": password, "api_key": api_key})
            csv_file.flush()
            print(f"  [+] Сохранено ({len(results)}/{ACCOUNTS_COUNT})")

            await ctx.close()

            if i < ACCOUNTS_COUNT:
                print(f"  [~] Пауза {DELAY_BETWEEN}с...")
                await asyncio.sleep(DELAY_BETWEEN)

        await browser.close()

    csv_file.close()
    _set_running_flag(False)
    print(f"\n[✓] Готово! {len(results)} аккаунтов в {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
