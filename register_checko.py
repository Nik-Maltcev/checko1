"""
Скрипт для массовой регистрации аккаунтов на checko.ru
с использованием временных email от mail.tm

Зависимости:
    pip install playwright requests python-dotenv
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
MAIL_TM_BASE = "https://api.mail.tm"
CHECKO_REGISTER = "https://checko.ru/sign-up"
CHECKO_PROFILE  = "https://checko.ru/user/account/api"
# ──────────────────────────────────────────────────────────────────────────────


def random_password(length: int = 14) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choices(chars, k=length))


def random_username(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


# ─── mail.tm helpers ──────────────────────────────────────────────────────────

def get_available_domain() -> str:
    """Получить первый доступный домен от mail.tm."""
    r = requests.get(f"{MAIL_TM_BASE}/domains", timeout=15)
    r.raise_for_status()
    domains = r.json().get("hydra:member", [])
    if not domains:
        raise RuntimeError("mail.tm не вернул ни одного домена")
    return domains[0]["domain"]


def create_temp_email(address: str, password: str) -> dict:
    """Создать аккаунт на mail.tm и вернуть данные."""
    r = requests.post(
        f"{MAIL_TM_BASE}/accounts",
        json={"address": address, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def get_mail_token(address: str, password: str) -> str:
    """Получить JWT-токен для доступа к почте."""
    r = requests.post(
        f"{MAIL_TM_BASE}/token",
        json={"address": address, "password": password},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def wait_for_confirmation_link(token: str, timeout: int = 120) -> str | None:
    """
    Ждать письмо с подтверждением и вернуть ссылку.
    Возвращает None если письмо не пришло за timeout секунд.
    """
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{MAIL_TM_BASE}/messages", headers=headers, timeout=15)
        if r.status_code == 200:
            messages = r.json().get("hydra:member", [])
            for msg in messages:
                # Получить полное тело письма
                msg_id = msg["id"]
                detail = requests.get(
                    f"{MAIL_TM_BASE}/messages/{msg_id}",
                    headers=headers,
                    timeout=15,
                ).json()
                body = detail.get("text", "") + detail.get("html", "")
                # Ищем ссылку подтверждения
                links = re.findall(r'https?://checko\.ru[^\s"\'<>]+', body)
                confirm_links = [
                    l for l in links
                    if any(kw in l for kw in ("confirm", "verify", "activate", "sign", "token", "email"))
                ]
                # Если специфичных нет — берём любую ссылку на checko.ru
                if not confirm_links:
                    confirm_links = links
                if confirm_links:
                    return confirm_links[0]
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


async def get_api_key(page) -> str | None:
    """
    Зайти на страницу API и вытащить ключ после текста 'Ваш API ключ'.
    """
    try:
        await page.goto(CHECKO_PROFILE, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(2000)

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

    # Получаем домен один раз
    domain = get_available_domain()
    print(f"[*] Используем домен: @{domain}")

    results = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        for i in range(1, ACCOUNTS_COUNT + 1):
            print(f"\n[{i}/{ACCOUNTS_COUNT}] Создаём аккаунт...")

            username = random_username()
            password = random_password()
            email = f"{username}@{domain}"

            # 1. Создать временный email
            try:
                create_temp_email(email, password)
                mail_token = get_mail_token(email, password)
                print(f"  [+] Временный email: {email}")
            except Exception as e:
                print(f"  [!] Ошибка создания email: {e}")
                continue

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
            confirm_url = wait_for_confirmation_link(mail_token, timeout=90)
            if confirm_url:
                print(f"  [+] Ссылка подтверждения: {confirm_url[:60]}...")
                await confirm_email_in_browser(page, confirm_url)
                print("  [+] Email подтверждён")
            else:
                print("  [~] Письмо не пришло (возможно, подтверждение не нужно)")

            # 4. Получить API-ключ
            api_key = await get_api_key(page)
            if api_key:
                print(f"  [+] API-ключ: {api_key[:20]}...")
            else:
                print("  [~] API-ключ не найден (проверь вручную)")
                api_key = "NOT_FOUND"

            results.append({
                "login": email,
                "password": password,
                "api_key": api_key,
            })

            await context.close()

            # Пауза между регистрациями
            if i < ACCOUNTS_COUNT:
                print(f"  [~] Пауза {DELAY_BETWEEN}с...")
                await asyncio.sleep(DELAY_BETWEEN)

        await browser.close()

    # 5. Сохранить CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["login", "password", "api_key"], delimiter="|")
        writer.writeheader()
        writer.writerows(results)

    _set_running_flag(False)
    print(f"\n[✓] Готово! Сохранено {len(results)} аккаунтов в {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
