"""
1. mail.tm — создать почту
2. checko.ru/sign-up — зарегистрироваться
3. mail.tm — перейти по ссылке подтверждения
4. checko.ru/login — войти
5. checko.ru/user/account/api — скопировать API ключ
6. Повторить 30 раз
"""

import asyncio
import csv
import random
import string
import time
import re
import os

import requests
from playwright.async_api import async_playwright

# ─── Настройки ────────────────────────────────────────────────────────────────
ACCOUNTS_COUNT = int(os.environ.get("ACCOUNTS_COUNT", 30))
OUTPUT_CSV     = os.environ.get("OUTPUT_CSV", "checko_accounts.csv")
HEADLESS       = os.environ.get("HEADLESS", "True").lower() != "false"
DELAY_BETWEEN  = int(os.environ.get("DELAY_BETWEEN", 5))
MAIL_TM        = "https://api.mail.tm"


def rand_pass(n=14):
    return "".join(random.choices(string.ascii_letters + string.digits + "!@#$", k=n))

def rand_user(n=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ─── mail.tm ──────────────────────────────────────────────────────────────────

def mailtm_domain():
    r = requests.get(f"{MAIL_TM}/domains", timeout=15)
    return r.json()["hydra:member"][0]["domain"]

def mailtm_create(addr, pwd):
    requests.post(f"{MAIL_TM}/accounts", json={"address": addr, "password": pwd}, timeout=15)

def mailtm_token(addr, pwd):
    r = requests.post(f"{MAIL_TM}/token", json={"address": addr, "password": pwd}, timeout=15)
    return r.json()["token"]

def mailtm_wait_link(token, timeout=60):
    """Ждём письмо от checko и возвращаем первую ссылку."""
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = requests.get(f"{MAIL_TM}/messages", headers=headers, timeout=15)
        for msg in r.json().get("hydra:member", []):
            detail = requests.get(f"{MAIL_TM}/messages/{msg['id']}", headers=headers, timeout=15).json()
            body = (detail.get("text", "") or "") + (detail.get("html", "") or "")
            links = re.findall(r'https?://checko\.ru[^\s"\'<>\)]+', body)
            links = [l for l in links if l.rstrip("/") != "https://checko.ru"]
            if links:
                return links[0]
        time.sleep(3)
    return None


# ─── Один аккаунт ─────────────────────────────────────────────────────────────

async def create_one_account(browser, email, password, token, idx):
    """Полный цикл: регистрация → подтверждение → логин → API ключ."""

    ctx = await browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
    )
    page = await ctx.new_page()

    try:
        # ── Шаг 1: Регистрация ──
        print(f"  [1] Открываю /sign-up...")
        await page.goto("https://checko.ru/sign-up", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        # Заполняем email
        await page.fill('input[type="email"]', email)

        # Заполняем оба поля пароля
        pwd_inputs = page.locator('input[type="password"]')
        for i in range(await pwd_inputs.count()):
            await pwd_inputs.nth(i).fill(password)

        # Ставим галочку
        cb = page.locator('input[type="checkbox"]')
        if await cb.count() > 0 and not await cb.first.is_checked():
            await cb.first.click(force=True)

        # Скриншот заполненной формы
        await page.screenshot(path=f"debug_filled_{idx}.png", full_page=True)
        print(f"  [~] Скриншот заполненной формы: debug_filled_{idx}.png")

        # Нажимаем "Зарегистрироваться"
        await page.click('button:has-text("Зарегистрироваться")')
        await page.wait_for_timeout(4000)

        url_after = page.url
        print(f"  [~] URL после регистрации: {url_after}")
        await page.screenshot(path=f"debug_after_{idx}.png", full_page=True)
        print(f"  [~] Скриншот после регистрации: debug_after_{idx}.png")

        if "sign-up" in url_after:
            # Форма не ушла — выводим текст ошибки
            text = await page.inner_text("body")
            print(f"  [!] Форма не отправилась. Текст страницы:\n{text[:500]}")
            return None

        # ── Шаг 2: Ждём письмо на mail.tm ──
        print(f"  [2] Ждём письмо от checko.ru...")
        link = mailtm_wait_link(token, timeout=60)
        if not link:
            print(f"  [!] Письмо не пришло за 60с")
            return None
        print(f"  [+] Ссылка подтверждения: {link[:80]}")

        # ── Шаг 3: Переходим по ссылке подтверждения ──
        print(f"  [3] Подтверждаю email...")
        await page.goto(link, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        print(f"  [~] URL после подтверждения: {page.url}")

        # ── Шаг 4: Логин ──
        print(f"  [4] Вхожу в аккаунт...")
        await page.goto("https://checko.ru/login", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1000)
        await page.fill('input[type="email"]', email)
        await page.fill('input[type="password"]', password)
        await page.click('button:has-text("Войти")')
        await page.wait_for_timeout(3000)
        print(f"  [~] URL после логина: {page.url}")

        # ── Шаг 5: Получаем API ключ ──
        print(f"  [5] Открываю страницу API...")
        await page.goto("https://checko.ru/user/account/api", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        print(f"  [~] URL страницы API: {page.url}")

        if "/login" in page.url:
            print(f"  [!] Редирект на логин — не авторизованы")
            return None

        await page.screenshot(path=f"debug_api_{idx}.png", full_page=True)

        # Берём текст страницы и ищем ключ
        text = await page.inner_text("body")
        print(f"  [~] Текст API страницы: {text[:400]}")

        # Ищем ключ — обычно после "Ваш API ключ"
        match = re.search(r'(?:Ваш API[- ]?ключ|Your API[- ]?key)[:\s]*([A-Za-z0-9_\-]{10,})', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

        # Может ключ просто длинная строка на странице
        match = re.search(r'\b[a-f0-9]{32,}\b', text)
        if match:
            return match.group(0)

        # Ищем в input
        for sel in ['input[readonly]', 'input[name*="api"]', 'input[id*="api"]', 'code']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=1000):
                    val = await el.input_value() if "input" in sel else await el.inner_text()
                    if len(val.strip()) > 10:
                        return val.strip()
            except:
                pass

        print(f"  [!] API ключ не найден")
        return None

    except Exception as e:
        print(f"  [!] Ошибка: {e}")
        return None
    finally:
        await ctx.close()


# ─── Main ─────────────────────────────────────────────────────────────────────

def _set_status(running, total=0):
    with open(".status", "w") as f:
        f.write(f"{running}|{total}")


async def main():
    print(f"[*] Старт: {ACCOUNTS_COUNT} аккаунтов")
    _set_status(True, ACCOUNTS_COUNT)

    domain = mailtm_domain()
    print(f"[*] Домен: @{domain}")

    results = []
    f = open(OUTPUT_CSV, "w", newline="", encoding="utf-8")
    w = csv.DictWriter(f, fieldnames=["login", "password", "api_key"], delimiter="|")
    w.writeheader()
    f.flush()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        for i in range(1, ACCOUNTS_COUNT + 1):
            print(f"\n{'='*50}")
            print(f"[{i}/{ACCOUNTS_COUNT}]")

            user = rand_user()
            email = f"{user}@{domain}"
            password = rand_pass()

            # Создаём почту
            try:
                mailtm_create(email, password)
                token = mailtm_token(email, password)
                print(f"  [+] Почта: {email}")
            except Exception as e:
                print(f"  [!] mail.tm ошибка: {e}")
                continue

            # Полный цикл
            api_key = await create_one_account(browser, email, password, token, i)

            row = {"login": email, "password": password, "api_key": api_key or "NOT_FOUND"}
            results.append(row)
            w.writerow(row)
            f.flush()

            if api_key:
                print(f"  [✓] Готово: {api_key[:24]}...")
            else:
                print(f"  [✗] API ключ не получен")

            if i < ACCOUNTS_COUNT:
                print(f"  [~] Пауза {DELAY_BETWEEN}с")
                await asyncio.sleep(DELAY_BETWEEN)

        await browser.close()

    f.close()
    _set_status(False)
    print(f"\n[✓] Итого: {len(results)} аккаунтов в {OUTPUT_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
