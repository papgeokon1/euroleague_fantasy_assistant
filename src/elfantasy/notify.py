"""Desktop notifications (Windows toasts), with a printed fallback elsewhere."""

import os
import subprocess
import sys

# Windows only shows toasts from a registered app id; PowerShell's is always present.
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

# A PowerShell type literal must stay on one line, so the lines are joined here.
# Title and body arrive through environment variables, never pasted into the script,
# so text with quotes or $ signs can't break or inject into the command.
SCRIPT = "\n".join(
    [
        "$m = [Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime]",
        "$kind = [Windows.UI.Notifications.ToastTemplateType]::ToastText02",
        "$t = $m::GetTemplateContent($kind)",
        "$x = $t.GetElementsByTagName('text')",
        "$x.Item(0).AppendChild($t.CreateTextNode($env:ELF_TOAST_TITLE)) > $null",
        "$x.Item(1).AppendChild($t.CreateTextNode($env:ELF_TOAST_BODY)) > $null",
        "$n = [Windows.UI.Notifications.ToastNotification]::new($t)",
        "$m::CreateToastNotifier($env:ELF_TOAST_APP).Show($n)",
    ]
)


def notify(title: str, body: str) -> bool:
    """Show a desktop notification. Returns False if it couldn't be shown (the text
    is printed either way)."""
    print(f"[notification] {title}: {body}")
    if sys.platform != "win32":
        return False
    env = {
        **os.environ,
        "ELF_TOAST_TITLE": title[:120],
        "ELF_TOAST_BODY": body[:400],
        "ELF_TOAST_APP": APP_ID,
    }
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
            env=env,
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no console flash
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
