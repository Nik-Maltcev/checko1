"""
Скрипт для массовой регистрации аккаунтов на checko.ru
с использованием временных email от 1secmail (без регистрации, без ключа)

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
from datetime import datetime

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ─── Настройки (можно переопределить через env-переменные) ───────────────────
ACCOUNTS_COUNT = int(os.environ.get("ACCOUNTS_COUNT", 30))
OUTPUT_CSV     = os.environ.get("OUTPUT_CSV", "checko_accounts.csv")
HEADLESS       = os.environ.get("HEADLESS", "True").lower() != "false"
DELAY_BETWEEN  = int(os.environ.get("DELAY_BETWEEN", 5))
CHECKO_REGISTER = "https://checko.ru/sign-up"
CHECKO_PROFILE  = "https://checko.ru/user/account/api"

# Guerrilla Mail API — бесплатно, без ключа, хорошая доставляемость
GUERRILLA_BASE = "https://api.guerrillamail.com/ajax.php"
GUERRILLA_DOMAINS = ["guerrillamailblock.com", "grr.la", "guerrillamail.info",
                     "sharklasers.com", "guerrillamail.biz", "spam4.me"]
# ──────────────────────────────────────────────────────────────────────────────


def random_password(length: int = 14) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choices(chars, k=length))


def random_username(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


# ─── Guerrilla Mail helpers ───────────────────────────────────────────────────

def create_temp_email() -> tuple[str, str, str, str]:
    """
    Создаём сессию на Guerrilla Mail.
    Возвращает (email, login, domain, sid_token).
    """
    domain = random.choice(GUERRILLA_DOMAINS)
    login  = random_username(12)
    r = requests.get(
        GUERRILLA_BASE,
        params={
            "f":          "set_email_user",
            "email_user": login,
            "lang":       "en",
            "site":       domain,
        },
        timeout=15,
    )
    data      = r.json()
    sid_token = data.get("sid_token", "")
    email     = data.get("email_addr", f"{login}@{domain}")
    # email может вернуться с другим доменом — берём как есть
    parts  = email.split("@")
    login  = parts[0]
    domain = parts[1] if len(parts) > 1 else domain
    return email, login, domain, sid_token


def wait_for_confirmation_link(login: str, domain: str, sid_token: str,
                                timeout: int = 120) -> str | None:
    """
    Ждать письмо с подтверждением на Guerrilla Mail и вернуть ссылку.
    """
    deadline = time.time() + timeout
    seq = 0
    while time.time() < deadline:
        try:
            r = requests.get(
                GUERRILLA_BASE,
                params={"f": "check_email", "seq": seq, "sid_token": sid_token},
                timeout=15,
            )
            data = r.json()
            messages = data.get("list", [])
            for msg in messages:
                msg_id = msg.get("mail_id")
                detail = requests.get(
                    GUERRILLA_BASE,
                    params={"f": "fetch_email", "email_id": msg_id,
                            "sid_token": sid_token},
                    timeout=15,
                ).json()
                body = detail.get("mail_body", "") + detail.get("mail_text_only", "")
                links = re.findall(r'https?://checko\.ru[^\s"\'<>]+', body)
                confirm_links = [
                    l for l in links
                    if any(kw in l for kw in ("confirm", "verify", "activate",
                                              "sign", "token", "email", "user"))
                ]
                if not confirm_links:
                    confirm_links = [l for l in links if l.rstrip("/") != "https://checko.ru"]
                if confirm_links:
                    return confirm_links[0]
            seq = data.get("seq", seq)
        except Exception as e:
            print(f"  [~] Ошибка проверки почты: {e}")
        time.sleep(5)
    return None


# ─── Playwright: регистрация ──────────────────────────────────────────────────

async def register_on_checko(
    page,
    email: str,
    password: str,
) -> bool:
    """
    Открыть страницу регистрации checko.ru и заполнить форму.
    Возвращает True при успехе.
    """
    try:
        await page.goto(CHECKO_REGISTER, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(1500)

        # Email
        await page.locator('input[type="email"]').first.fill(email)

        # Пароль + подтверждение — все input[type=password] на странице
        pwd_fields = page.locator('input[type="password"]')
        count = await pwd_fields.count()
        for i in range(count):
            await pwd_fields.nth(i).fill(password)

        # Чекбокс согласия с пользовательским соглашением
        checkbox = page.locator('input[type="checkbox"]').first
        if await checkbox.is_visible():
            is_checked = await checkbox.is_checked()
            if not is_checked:
                await checkbox.check()

        await page.wait_for_timeout(500)

        # Кнопка "Зарегистрироваться"
        submit = page.locator('button:has-text("Зарегистрироваться")').first
        if not await submit.is_visible():
            submit = page.locator('button[type="submit"]').first
        await submit.click()

        await page.wait_for_timeout(3000)

        # Логируем URL после отправки — сразу видно что произошло
        current_url = page.url
        print(f"  [~] URL после формы: {current_url}")

        # Скриншот для отладки — сохраняем первые 3
        try:
            import glob
            existing = glob.glob("debug_*.png")
            if len(existing) < 3:
                idx = len(existing) + 1
                await page.screenshot(path=f"debug_{idx}.png", full_page=True)
                print(f"  [~] Скриншот: debug_{idx}.png")
        except Exception:
            pass

        # Проверяем нет ли ошибки на странице
        body_text = await page.inner_text("body")
        error_keywords = ["недопустим", "некорректн", "уже зарегистрирован",
                          "ошибка", "error", "invalid", "already"]
        for kw in error_keywords:
            if kw.lower() in body_text.lower():
                print(f"  [!] Возможная ошибка на странице: найдено '{kw}'")
                break

        return True

    except PlaywrightTimeout as e:
        print(f"  [!] Timeout при регистрации: {e}")
        return False
    except Exception as e:
        print(f"  [!] Ошибка при регистрации: {e}")
        return False


async def confirm_email_in_browser(page, confirm_url: str) -> bool:
    """Перейти по ссылке подтверждения email."""
    try:
        await page.goto(confirm_url, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(2000)
        # Скриншот после подтверждения
        try:
            import glob
            existing = glob.glob("debug_confirm_*.png")
            if len(existing) < 3:
                idx = len(existing) + 1
                await page.screenshot(path=f"debug_confirm_{idx}.png", full_page=True)
                print(f"  [~] Скриншот подтверждения: debug_confirm_{idx}.png")
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"  [!] Ошибка подтверждения email: {e}")
        return False


async def login_on_checko(page, email: str, password: str) -> bool:
    """Залогиниться после подтверждения email."""
    try:
        current_url = page.url
        # Если уже залогинены — не нужно
        if "login" not in current_url and "sign" not in current_url:
            print(f"  [~] Уже авторизованы (URL: {current_url})")
            return True

        await page.goto("https://checko.ru/login", wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(1000)

        await page.locator('input[type="email"]').first.fill(email)
        pwd = page.locator('input[type="password"]').first
        await pwd.fill(password)

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


async def get_api_key(page) -> str | None:
    """
    Зайти на страницу API и вытащить ключ после текста 'Ваш API ключ'.
    """
    try:
        await page.goto(CHECKO_PROFILE, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(2000)

        print(f"  [~] URL страницы API: {page.url}")

        # Скриншот страницы API для отладки
        try:
            import glob
            existing = glob.glob("debug_api_*.png")
            if len(existing) < 3:
                idx = len(existing) + 1
                await page.screenshot(path=f"debug_api_{idx}.png", full_page=True)
                print(f"  [~] Скриншот API страницы: debug_api_{idx}.png")
        except Exception:
            pass

        # Вариант 1: ищем элемент рядом с текстом "Ваш API ключ"
        # Пробуем взять следующий sibling или вложенный элемент
        selectors = [
            # input или code внутри блока с API ключом
            'input[name*="api"]',
            'input[id*="api"]',
            'input[value*="api"]',
            '.api-key',
            '#api-key',
            'code',
            'pre',
        ]
        for sel in selectors:
            el = page.locator(sel).first
            if await el.is_visible():
                val = await el.input_value() if sel.startswith('input') else await el.inner_text()
                val = val.strip()
                if len(val) > 8:
                    print(f"  [+] API ключ найден через селектор '{sel}'")
                    return val

        # Вариант 2: парсим текст страницы — берём слово после "Ваш API ключ"
        content = await page.inner_text("body")
        match = re.search(r'Ваш API ключ[:\s]+([A-Za-z0-9_\-]{8,})', content)
        if match:
            return match.group(1).strip()

        # Вариант 3: ищем в HTML — вдруг ключ в value или data-атрибуте
        html = await page.content()
        match = re.search(r'Ваш API ключ.*?([A-Za-z0-9_\-]{20,})', html, re.DOTALL)
        if match:
            return match.group(1).strip()

        print("  [!] API ключ не найден на странице")
        return None

    except Exception as e:
        print(f"  [!] Ошибка получения API-ключа: {e}")
        return None


# ─── Основной цикл ────────────────────────────────────────────────────────────

def _set_running_flag(running: bool, total: int = 0):
    """Пишем флаг статуса в файл — Flask читает его."""
    with open(".status", "w") as f:
        f.write(f"{running}|{total}")


async def main():
    print(f"[*] Старт. Создаём {ACCOUNTS_COUNT} аккаунтов...")
    _set_running_flag(True, ACCOUNTS_COUNT)

    results = []

    # Открываем CSV сразу — пишем построчно чтобы не терять данные при сбое
    csv_file = open(OUTPUT_CSV, "w", newline="", encoding="utf-8")
    csv_writer = csv.DictWriter(csv_file, fieldnames=["login", "password", "api_key"], delimiter="|")
    csv_writer.writeheader()
    csv_file.flush()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        for i in range(1, ACCOUNTS_COUNT + 1):
            print(f"\n[{i}/{ACCOUNTS_COUNT}] Создаём аккаунт...")

            password = random_password()

            # 1. Генерируем временный email (Guerrilla Mail — без регистрации)
            email, mail_login, mail_domain, sid_token = create_temp_email()
            print(f"  [+] Временный email: {email}")

            # 2. Регистрация на checko.ru
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            success = await register_on_checko(page, email, password)
            if not success:
                print("  [!] Регистрация не удалась, пропускаем")
                await context.close()
                continue

            print("  [+] Форма отправлена, ждём письмо...")

            # 3. Ждём письмо с подтверждением
            confirm_url = wait_for_confirmation_link(mail_login, mail_domain, sid_token, timeout=90)
            if confirm_url:
                print(f"  [+] Ссылка подтверждения: {confirm_url[:60]}...")
                await confirm_email_in_browser(page, confirm_url)
                print("  [+] Email подтверждён")
                # После подтверждения может быть редирект на /login — логинимся
                await login_on_checko(page, email, password)
            else:
                print("  [~] Письмо не пришло за 90с — пробуем войти напрямую")
                await login_on_checko(page, email, password)

            # 4. Получить API-ключ
            api_key = await get_api_key(page)
            if api_key:
                print(f"  [+] API-ключ: {api_key[:20]}...")
            else:
                print("  [~] API-ключ не найден (проверь вручную)")
                api_key = "NOT_FOUND"

            results.append({"login": email, "password": password, "api_key": api_key})

            # Пишем сразу — данные не потеряются при сбое
            csv_writer.writerow({"login": email, "password": password, "api_key": api_key})
            csv_file.flush()
            print(f"  [+] Сохранено в CSV ({len(results)}/{ACCOUNTS_COUNT})")

            await context.close()

            # Пауза между регистрациями
            if i < ACCOUNTS_COUNT:
                print(f"  [~] Пауза {DELAY_BETWEEN}с...")
                await asyncio.sleep(DELAY_BETWEEN)

        await browser.close()

    csv_file.close()

    # Итог
    _set_running_flag(False)
    print(f"\n[✓] Готово! Сохранено {len(results)} аккаунтов в {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
