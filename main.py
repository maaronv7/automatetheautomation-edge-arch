import asyncio
import subprocess
import os
import pyautogui as pag
import time
import logging
import sys
import json
import tkinter as tk
from tkinter import filedialog
import mss
import cv2
import numpy as np
from datetime import datetime, time as time2, timedelta

# ── NOTE: Install dependencies on Arch Linux ──────────────────────────────────
# sudo pacman -S python python-pip wmctrl xdotool xorg-xdpyinfo
# pip install pyautogui mss opencv-python numpy pillow
#
# Install Microsoft Edge on Arch Linux (AUR):
#   yay -S microsoft-edge-stable-bin
# Or via flatpak:
#   flatpak install flathub com.microsoft.Edge
# ─────────────────────────────────────────────────────────────────────────────

pag.FAILSAFE = False
# pag.PAUSE = 0.5

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('.venv/redeemableAccounts.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)


# ── Window helpers (replaces pygetwindow on Linux) ────────────────────────────

def get_windows_with_title(title_fragment: str) -> list[dict]:
    """
    Returns a list of window dicts {id, title} whose title contains
    title_fragment (case-insensitive). Uses xdotool.
    """
    try:
        output = subprocess.check_output(
            ["xdotool", "search", "--name", title_fragment],
            text=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return []
    except FileNotFoundError:
        raise RuntimeError("xdotool is not installed. Run: sudo pacman -S xdotool")

    windows = []
    for win_id in output.strip().splitlines():
        win_id = win_id.strip()
        if not win_id:
            continue
        try:
            title = subprocess.check_output(
                ["xdotool", "getwindowname", win_id],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
            windows.append({"id": win_id, "title": title})
        except subprocess.CalledProcessError:
            continue

    return windows


def activate_window(win_id: str):
    """Bring window to foreground using xdotool."""
    subprocess.call(
        ["xdotool", "windowactivate", "--sync", win_id],
        stderr=subprocess.DEVNULL
    )
    time.sleep(0.3)


def load_apps_to_close(file_path: str) -> list:
    with open(file_path, "r") as f:
        data = json.load(f)
    return data["apps"]


def close_apps_by_title(windows: list):
    """
    Close all windows whose title contains any string in `windows`.
    Uses xdotool to find and send WM_DELETE_WINDOW (graceful close).
    """
    for title_fragment in windows:
        try:
            output = subprocess.check_output(
                ["xdotool", "search", "--name", title_fragment],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
        except subprocess.CalledProcessError:
            logging.info(f"No windows found matching: {title_fragment}")
            continue

        for win_id in output.splitlines():
            win_id = win_id.strip()
            if not win_id:
                continue
            try:
                subprocess.call(
                    ["xdotool", "windowclose", win_id],
                    stderr=subprocess.DEVNULL
                )
                logging.info(f"Closed window {win_id} (matched '{title_fragment}')")
            except Exception as e:
                logging.warning(f"Could not close window {win_id}: {e}")
            time.sleep(1)


# ── Profile discovery ─────────────────────────────────────────────────────────

def get_profile_folders(user_data_dir: str, target_folder: str):
    """
    Scan user_data_dir for Edge profiles that have the target extension installed.
    Edge uses the same profile folder structure as Chrome (Profile 1, Profile 2…).
    The Default profile is also included if it has the extension.
    """
    try:
        profile_folders = []
        for item in os.listdir(user_data_dir):
            full_path = os.path.join(user_data_dir, item)
            # Edge uses "Default" and "Profile N" — include both
            if os.path.isdir(full_path) and (
                item.startswith("Profile ") or item == "Default"
            ):
                extension_settings_path = os.path.join(
                    full_path, "Local Extension Settings", target_folder
                )
                if os.path.exists(extension_settings_path):
                    profile_folders.append(item)

        profile_folders.sort()
        print(f"Found {len(profile_folders)} profiles with the extension: {profile_folders}")
    except Exception as e:
        logging.error(f"Error finding profile folders: {e}")
        raise
    return profile_folders, len(profile_folders)


def get_profile_folders_by_email(user_data_dir: str, target_folder: str, email_batch: list):
    """
    Like get_profile_folders but filtered to a specific list of emails.
    Reads the Preferences file in each Edge profile to get the signed-in account.
    """
    try:
        profile_folders = []
        email_to_profile: dict[str, tuple[str, bool]] = {}

        for item in os.listdir(user_data_dir):
            full_path = os.path.join(user_data_dir, item)
            prefs_path = os.path.join(full_path, "Preferences")

            # Edge uses "Default" and "Profile N"
            if not (
                os.path.isdir(full_path)
                and (item.startswith("Profile ") or item == "Default")
                and os.path.exists(prefs_path)
            ):
                continue

            extension_settings_path = os.path.join(
                full_path, "Local Extension Settings", target_folder
            )

            with open(prefs_path, "r", encoding="utf-8") as f:
                prefs = json.load(f)

            # Edge stores the signed-in account under account_info (same as Chrome)
            account_info = prefs.get("account_info", [])
            email = account_info[0].get("email") if account_info else None

            if email:
                email_to_profile[email] = (item, os.path.exists(extension_settings_path))

        for email in email_batch:
            if email not in email_to_profile:
                logging.warning(f"The email '{email}' is not a profile.")
                continue

            profile_folder, has_extension = email_to_profile[email]
            if not has_extension:
                logging.warning(f"The email '{email}' does not have the extension.")
                continue

            profile_folders.append(profile_folder)

        profile_folders.sort()
        print(f"Found {len(profile_folders)} profiles with the extension: {profile_folders}")

    except Exception as e:
        logging.error(f"Error finding profile folders: {e}")
        raise

    return profile_folders, len(profile_folders)


# ── Edge launcher ─────────────────────────────────────────────────────────────

def open_edge_profiles(edge_path: str, user_data_dir: str,
                       profile_folders: list, num_of_profiles: int) -> list[dict]:
    """
    Launch one Edge window per profile, all navigating to example.com,
    then wait until we can see all windows in the task list.
    Edge accepts the same --user-data-dir / --profile-directory flags as Chrome.
    """
    try:
        for profile_name in profile_folders:
            print(f"Opening {profile_name}...")
            subprocess.Popen([
                edge_path,
                f"--user-data-dir={user_data_dir}",
                f"--profile-directory={profile_name}",
                "https://www.example.com"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
            time.sleep(1)

        # Edge window titles look like "Example Domain - Microsoft​ Edge"
        edge_windows = []
        tries = 7
        for _ in range(tries):
            edge_windows = get_windows_with_title("Example Domain")
            if len(edge_windows) == num_of_profiles:
                break
            time.sleep(3)
            print("No Edge windows with 'Example Domain' found, retrying...")

        print(f"Found {len(edge_windows)} Edge windows with the title 'Example Domain'.")
    except Exception as e:
        logging.error(f"Error opening Edge profiles: {e}")
        raise
    return edge_windows


def open_edge_profiles_rewards(edge_path: str, user_data_dir: str,
                                profile_folders: list, num_of_profiles: int):
    """
    Launch Edge for each profile pointed at Bing (for the rewards search flow).
    Does not wait for windows — caller handles timing.
    """
    try:
        for profile_name in profile_folders:
            print(f"Opening {profile_name}...")
            subprocess.Popen([
                edge_path,
                f"--user-data-dir={user_data_dir}",
                f"--profile-directory={profile_name}",
                "https://www.bing.com/"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
            time.sleep(1)
    except Exception as e:
        logging.error(f"Error opening Edge profiles: {e}")
        raise


# ── Button clicker ────────────────────────────────────────────────────────────

def click_desktop_button(edge_windows: list[dict], image_path: str, max_retries: int = 5):
    try:
        template = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if template is None:
            raise FileNotFoundError(f"Template image not found: {image_path}")
        template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
        h, w = template_gray.shape

        failed_windows = list(edge_windows)

        for attempt in range(1, max_retries + 1):
            if not failed_windows:
                break

            logging.info(f"Attempt {attempt}/{max_retries} — processing {len(failed_windows)} window(s).")
            still_failing = []

            for win in failed_windows:
                activate_window(win["id"])
                time.sleep(0.5)
                pag.hotkey('ctrl', 'shift', 'r')
                time.sleep(1)

                match_found = False
                with mss.mss() as sct:
                    for monitor in sct.monitors[1:]:
                        screenshot = np.array(sct.grab(monitor))
                        screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGRA2GRAY)
                        result = cv2.matchTemplate(screenshot_gray, template_gray, cv2.TM_CCOEFF_NORMED)
                        _, max_val, _, max_loc = cv2.minMaxLoc(result)
                        if max_val >= 0.5:
                            global_x = monitor["left"] + max_loc[0] + w // 2
                            global_y = monitor["top"] + max_loc[1] + h // 2
                            pag.click(x=global_x, y=global_y)
                            match_found = True
                            break

                if not match_found:
                    logging.warning(
                        f"Attempt {attempt}: Button not found for window {win['id']}. Queuing for retry."
                    )
                    still_failing.append(win)

            failed_windows = still_failing

        if failed_windows:
            failed_ids = [w["id"] for w in failed_windows]
            logging.error(f"Button not found after {max_retries} attempt(s) for window(s): {failed_ids}")

    except Exception as e:
        logging.error(f"Error clicking desktop button: {e}")


def wait_until_time(target_hour=0, target_minute=1):
    """Wait until the specified time (default 12:01 AM)."""
    while True:
        now = datetime.now()
        target = time2(target_hour, target_minute)

        if now.hour == target_hour and now.minute == target_minute:
            print(f"Time reached: {now.strftime('%H:%M:%S')}")
            break

        target_datetime = now.replace(
            hour=target_hour, minute=target_minute, second=0, microsecond=0
        )
        if target_datetime <= now:
            target_datetime += timedelta(days=1)

        seconds_until = (target_datetime - now).total_seconds()
        print(
            f"Waiting until {target_hour:02d}:{target_minute:02d} "
            f"({seconds_until:.0f} seconds remaining)"
        )
        time.sleep(min(60, seconds_until))

# ── File helpers ──────────────────────────────────────────────────────────────

def open_file_selector_json() -> str:
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    file_path = filedialog.askopenfilename(
        title="Select Email Batch JSON",
        filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
    )
    root.destroy()
    return file_path


def load_email_batches_json(file_path: str) -> list:
    with open(file_path, "r") as f:
        data = json.load(f)
    return data["batches"]


# ── Configuration ─────────────────────────────────────────────────────────────
# Update these paths for your setup.

HOME = os.path.expanduser("~")

# ── Edge user data directory ───────────────────────────────────────────────────
# Native install (yay -S microsoft-edge-stable-bin):
#   ~/.config/microsoft-edge
# Flatpak install (flatpak install com.microsoft.Edge):
#   ~/.var/app/com.microsoft.Edge/config/microsoft-edge

user_data_dir = "/home/deck/.var/app/com.microsoft.Edge/config/microsoft-edge"
# user_data_dir = f"{HOME}/.var/app/com.microsoft.Edge/config/microsoft-edge"  # flatpak

# ── Edge executable ────────────────────────────────────────────────────────────
# Native:
edge_path = "/var/lib/flatpak/exports/bin/com.microsoft.Edge"
# Flatpak:
# edge_path = "/var/lib/flatpak/exports/bin/com.microsoft.Edge"

# ── Extension ID ──────────────────────────────────────────────────────────────
# Replace with your extension's ID from edge://extensions
# (the same 32-char lowercase string shown under "Details")
target_folder = "padcfmjlalcdcgoamlodlienichjegjd"  # ← update if different on Edge

image_path    = "DesktopButton.png"
JSON_FILE_PATH = "my_email.json"
APP_FILE_PATH  = "/home/deck/PycharmProjects/automatetheautomation-edge-arch/apps_to_close.json"
config_path    = "/home/deck/PycharmProjects/rewards-automator/Rewards-Search-Automator/js/config.json"

with open(config_path) as f:
    searches = json.load(f)["searches"]

profile_folders: list = []
num_of_profiles: int = 0

with open(JSON_FILE_PATH) as f:
    batches = json.load(f)["batches"]

print("\n📧 BATCHES:")
for i, batch in enumerate(batches, 1):
    print(f"Batch {i}: {batch}")

print(f"\n🔍 SEARCHES:")
print(f"Min: {searches['millisecondsMin'] / 1000:.0f}s")
print(f"Max: {searches['millisecondsMax'] / 1000:.0f}s")
print(f"Desktop: {searches['desktop']}")

print("\n" + "="*80)

# ── Entry point ───────────────────────────────────────────────────────────────

print("Select if Batch (Y/N):")
isBatch = input().strip().upper()

if isBatch == "Y":
    #wait_until_time(0, 5)
    email_matrix = load_email_batches_json(JSON_FILE_PATH)
    for i, email_batch in enumerate(email_matrix):
        print(f"\n--- Processing Batch {i + 1}: {email_batch} ---")
        sleep_duration = int(
            (searches["millisecondsMax"] + searches["millisecondsMin"]) * 0.5 / 1000
            * searches["desktop"]
        )
        wake_time = datetime.now() + timedelta(seconds=sleep_duration)
        print(
            f"Sleeping for {sleep_duration // 60} min {sleep_duration % 60} sec… "
            f"Will resume at {wake_time.strftime('%I:%M:%S %p')}"
        )

        profile_folders, num_of_profiles = get_profile_folders_by_email(
            user_data_dir, target_folder, email_batch
        )
        edge_windows = open_edge_profiles(
            edge_path, user_data_dir, profile_folders, num_of_profiles
        )
        click_desktop_button(edge_windows, image_path)
        time.sleep(sleep_duration)
        #windows_to_close = load_apps_to_close(APP_FILE_PATH)
        # close_apps_by_title(windows_to_close)
        time.sleep(300)

elif isBatch == "N":
    profile_folders, num_of_profiles = get_profile_folders(user_data_dir, target_folder)
    edge_windows = open_edge_profiles(
        edge_path, user_data_dir, profile_folders, num_of_profiles
    )
    click_desktop_button(edge_windows, image_path)

elif isBatch == "X":
    email_matrix = load_email_batches_json(JSON_FILE_PATH)
    for i, email_batch in enumerate(email_matrix):
        print(f"\n--- Processing Batch {i + 1}: {email_batch} ---")
        profile_folders, num_of_profiles = get_profile_folders_by_email(
            user_data_dir, target_folder, email_batch
        )
        open_edge_profiles_rewards(
            edge_path, user_data_dir, profile_folders, num_of_profiles
        )

else:
    print("Invalid input. Please enter 'Y', 'N', or 'X'.")
    sys.exit(1)