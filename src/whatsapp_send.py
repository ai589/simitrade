# Send a message to your own WhatsApp self-chat, via agent-browser.
#
# There is no WhatsApp API here. Everything on this machine drives WhatsApp Web's DOM in a
# real, logged-in Chrome. This mirrors the mechanism the bepc-whatsapp-daily-scan skill
# uses (selectors verified 2026-09-01), packaged as something a script can call.
#
# Two non-obvious details that the existing skills learned the hard way:
#   * The composer is a Lexical editor. execCommand('insertText') does nothing and
#     navigator.clipboard.writeText is blocked by permissions. The only way to get text in
#     - and the only way to get NEWLINES into a single message rather than one message per
#     line - is to dispatch a synthetic ClipboardEvent('paste') carrying a DataTransfer.
#   * Do not run headless. WhatsApp Web resists it.
#
# Sending is OFF unless you pass --send. A silent no-op is the documented failure mode, so
# every send is verified afterwards (composer emptied, message present, no error icon).
#
#   python src/whatsapp_send.py --text "hello"            dry run: open, find chat, stop
#   python src/whatsapp_send.py --text "hello" --send     actually send
#   python src/whatsapp_send.py --file msg.txt --send
#   python src/whatsapp_send.py --check                   is the session still logged in?
#
# Requires a warm profile, seeded once:
#   python src/whatsapp_send.py --login    open WhatsApp Web and scan the QR from your phone

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import sys
import time

# Its own profile, not the daily scan's. Two reasons: Chrome refuses to launch on the
# bepc-daily-scan profile while that scan holds it (exit code 21, no DevToolsActivePort),
# and the scan runs at 07:45 - twenty minutes before this brief - so they would collide
# regularly. Costs one QR scan: python src/whatsapp_send.py --login
DEFAULT_PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                               "ecl-trade-brief", "chrome-profile")
DEFAULT_SESSION = "ecl-trade-brief"
DEFAULT_CHAT = "Chen Lian (You)"
SELF_CHAT_HEADER = "Message yourself"

# agent-browser's bundled Chromium is rejected by WhatsApp Web with "WhatsApp works with
# Google Chrome 100+ ... Update Google Chrome", so drive the real Chrome instead.
CHROME_CANDIDATES = [
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                 "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 "Google", "Chrome", "Application", "chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 "Google", "Chrome", "Application", "chrome.exe"),
]


def default_chrome():
    for p in CHROME_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


COMPOSER = '[contenteditable="true"][data-tab="10"]'
# WhatsApp changes selectors without notice; the second form is what the older
# Chrome-extension skills use, kept as a fallback.
COMPOSER_FALLBACK = 'footer div[contenteditable="true"][role="textbox"]'


class WhatsAppError(RuntimeError):
    pass


def _exe():
    for name in ("agent-browser.cmd", "agent-browser"):
        p = shutil.which(name)
        if p:
            return p
    raise WhatsAppError("agent-browser not found on PATH. "
                        "Expected ~/AppData/Local/hermes/node/agent-browser.")


def ab(*args, profile=DEFAULT_PROFILE, session=DEFAULT_SESSION, timeout=90, check=True,
       executable=None):
    """One agent-browser call. shell=False, so message text never touches a shell.

    Output goes to temp FILES, not pipes. agent-browser leaves a browser daemon running
    that inherits whatever stdout it was given, so capture_output=True never returns - the
    read blocks until every writer closes, and the daemon never does. Same reason for
    stdin=DEVNULL."""
    cmd = [_exe(), "--session", session, "--profile", profile]
    exe = executable if executable is not None else default_chrome()
    if exe:
        cmd += ["--executable-path", exe]
    cmd += [str(a) for a in args]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as fh:
        try:
            rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise WhatsAppError("agent-browser %s timed out after %ds." % (args[0], timeout))
        fh.seek(0)
        out = fh.read()
    if check and rc != 0:
        raise WhatsAppError("agent-browser %s failed (%d):\n%s"
                            % (args[0], rc, out.strip()[:800]))
    return _clean(out)


_NOISE = ("--profile ignored", "Use 'agent-browser close'", "Update available",
          "npm i -g", "Changelog:")


def _clean(out):
    """agent-browser prints advisory banners on stdout alongside the result, and they are
    not errors, so they end up mixed into eval output. Keep only the payload."""
    lines = [ln.rstrip() for ln in (out or "").splitlines()]
    lines = [ln for ln in lines if ln.strip() and not any(n in ln for n in _NOISE)]
    return "\n".join(lines).strip()


def _result(out):
    """The value of an `eval` is the last payload line."""
    lines = [ln for ln in _clean(out).splitlines() if ln.strip()]
    return lines[-1].strip().strip('"') if lines else ""


# ---- JavaScript payloads -------------------------------------------------------------
#
# Every payload is collapsed to ONE line before it is handed to `agent-browser eval`.
# Multi-line source is accepted but evaluates to null, which reads exactly like a page
# that has not loaded - so it is worth keeping this non-negotiable. None of these use //
# comments, so collapsing on whitespace is safe.

def _oneline(js):
    return " ".join(ln.strip() for ln in js.strip().splitlines() if ln.strip())


JS_STATE = """
(() => {
  const t = document.body.innerText || '';
  const qr = document.querySelector('canvas[aria-label*="scan" i], [data-testid="qrcode"]');
  if (qr || /Log in to WhatsApp|Steps to log in|Link a device/i.test(t)) return 'LOGIN';
  if (/Your messages are downloading|Don.t close this window/i.test(t)) return 'SYNCING';
  if (document.querySelector('#main')) return 'CHAT_OPEN';
  if (document.querySelector('#pane-side')) return 'READY';
  return 'UNKNOWN';
})()
"""

JS_HEADER = "(() => (document.querySelector('#main header')||{}).innerText || '')()"

JS_LAST_MESSAGES = """
(() => {
  const rows = [...document.querySelectorAll('#main [data-testid="msg-container"], #main div.message-out')];
  return rows.slice(-%d).map(r => r.innerText || '').join('\\n---\\n');
})()
"""


def js_paste(text):
    return """
(() => {
  const msg = %s;
  const box = document.querySelector(%s) || document.querySelector(%s);
  if (!box) return 'NO_BOX';
  box.focus();
  const dt = new DataTransfer();
  dt.setData('text/plain', msg);
  box.dispatchEvent(new ClipboardEvent('paste',
    { clipboardData: dt, bubbles: true, cancelable: true }));
  return 'PASTED ' + msg.length;
})()
""" % (json.dumps(text), json.dumps(COMPOSER), json.dumps(COMPOSER_FALLBACK))


JS_ENTER = """
(() => {
  const box = document.querySelector(%s) || document.querySelector(%s);
  if (!box) return 'NO_BOX';
  box.focus();
  box.dispatchEvent(new KeyboardEvent('keydown',
    { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true }));
  return 'SENT';
})()
""" % (json.dumps(COMPOSER), json.dumps(COMPOSER_FALLBACK))

JS_VERIFY = """
(() => {
  const box = document.querySelector(%s) || document.querySelector(%s);
  const rows = [...document.querySelectorAll('#main [data-testid="msg-container"]')];
  const last = rows.length ? (rows[rows.length - 1].innerText || '') : '';
  return JSON.stringify({
    composerEmpty: !box || (box.innerText || '').trim().length === 0,
    lastMessage: last.slice(0, 400),
    error: !!document.querySelector('#main [data-icon="ic-error"]')
  });
})()
""" % (json.dumps(COMPOSER), json.dumps(COMPOSER_FALLBACK))


# ---- flow -----------------------------------------------------------------------------

def _state(**kw):
    return _result(ab("eval", _oneline(JS_STATE), **kw))


def _open_whatsapp(verbose=True, **kw):
    """Navigate to WhatsApp Web in a browser we know is using our profile.

    agent-browser keeps one daemon per session, and if a daemon is already up it prints
    "--profile ignored: daemon already running" and carries on with whatever profile it
    started with. That succeeds (exit 0) while silently using the wrong - unauthenticated
    - profile, so we always close first rather than trying to detect it afterwards."""
    ab("close", check=False, timeout=60, **kw)
    time.sleep(2)
    try:
        return ab("open", "https://web.whatsapp.com", timeout=180, **kw)
    except WhatsAppError as e:
        if "exited early" in str(e) or "DevToolsActivePort" in str(e):
            raise WhatsAppError(
                "Chrome would not start on profile %s.\n"
                "Usually that means another Chrome already holds it. Close it, or point "
                "--profile at a different directory and run --login once.\n%s"
                % (kw.get("profile", DEFAULT_PROFILE), e))
        raise


def open_self_chat(chat=DEFAULT_CHAT, verbose=True, **kw):
    """Open WhatsApp Web and land in the self-chat. Raises if not logged in."""
    if verbose:
        print("opening WhatsApp Web...")
    _open_whatsapp(verbose=verbose, **kw)

    for attempt in range(30):                      # up to ~60s for sync to finish
        st = _state(**kw)
        if st == "LOGIN":
            raise WhatsAppError(
                "WhatsApp Web is showing the login screen - the session has expired.\n"
                "Re-link on your phone (WhatsApp > Linked devices), then run this once "
                "with a headed browser to scan the QR.")
        if st in ("READY", "CHAT_OPEN"):
            break
        time.sleep(2)
    else:
        raise WhatsAppError("WhatsApp Web did not finish loading (state stuck at %s)." % st)

    header = _result(ab("eval", _oneline(JS_HEADER), **kw))
    if SELF_CHAT_HEADER.lower() not in header.lower():
        if verbose:
            print("opening chat %r..." % chat)
        # The documented new-chat / message-yourself testids did not exist in the
        # 2026-09-01 build; clicking the row by its accessible text is what works.
        ab("find", "text", chat, "click", **kw)
        time.sleep(2)
        header = _result(ab("eval", _oneline(JS_HEADER), **kw))

    if SELF_CHAT_HEADER.lower() not in header.lower():
        raise WhatsAppError(
            "Refusing to continue: the open chat does not look like your self-chat.\n"
            "Header reads %r, expected it to contain %r. Sending into the wrong chat is "
            "not recoverable." % (header[:120], SELF_CHAT_HEADER))
    if verbose:
        print("self-chat open (header: %s)" % header.replace("\n", " / ")[:80])
    return header


def already_sent(marker, lookback=6, **kw):
    """True if `marker` already appears in the last few messages - so a re-run on the
    same day does not double-send."""
    if not marker:
        return False
    recent = _clean(ab("eval", _oneline(JS_LAST_MESSAGES % lookback), **kw))
    return marker in recent


def send(text, dry_run=True, chat=DEFAULT_CHAT, marker=None, verbose=True, **kw):
    """Send `text` to the self-chat. Returns True if a message was actually sent."""
    if not text or not text.strip():
        raise WhatsAppError("Refusing to send an empty message.")
    open_self_chat(chat=chat, verbose=verbose, **kw)

    if marker and already_sent(marker, **kw):
        if verbose:
            print("already sent (marker %r found in recent messages) - skipping." % marker)
        return False

    if dry_run:
        print("\nDRY RUN - nothing sent. %d characters ready for %s:\n" % (len(text), chat))
        print("-" * 60)
        print(text)
        print("-" * 60)
        print("\nAdd --send to actually send.")
        return False

    if _result(ab("eval", _oneline(js_paste(text)), **kw)) == "NO_BOX":
        raise WhatsAppError("Could not find the message composer - WhatsApp's selectors "
                            "have probably changed. Check COMPOSER in this file.")
    time.sleep(1)
    ab("eval", _oneline(JS_ENTER), **kw)
    time.sleep(2)

    raw = _result(ab("eval", _oneline(JS_VERIFY), **kw))
    try:
        v = json.loads(json.loads(raw)) if raw.startswith('"') else json.loads(raw)
    except (ValueError, TypeError):
        raise WhatsAppError("Sent, but could not verify - raw response: %s" % raw[:300])
    if v.get("error"):
        raise WhatsAppError("WhatsApp shows a send-failure icon on the last message.")
    if not v.get("composerEmpty"):
        raise WhatsAppError("Composer is not empty - the message probably did NOT send.")
    if verbose:
        print("sent and verified. Last message begins: %r"
              % (v.get("lastMessage") or "")[:80])
    return True


def login(wait_minutes=5, **kw):
    """One-off: open WhatsApp Web in this profile and wait for the QR to be scanned."""
    print("Opening WhatsApp Web. A Chrome window will appear.")
    print("On your phone: WhatsApp > Settings > Linked devices > Link a device, "
          "then scan the QR.\n")
    _open_whatsapp(**kw)
    deadline = time.time() + wait_minutes * 60
    last = None
    while time.time() < deadline:
        st = _state(**kw)
        if st != last:
            print("  state: %s" % st)
            last = st
        if st in ("READY", "CHAT_OPEN"):
            print("\nLogged in. The profile is now warm; future runs need no QR.")
            print("Verify with: python src/whatsapp_send.py --check")
            return 0
        time.sleep(5)
    print("\nStill not logged in after %d minutes. Re-run --login when ready."
          % wait_minutes, file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description="Send a message to your WhatsApp self-chat.")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--text", help="message text")
    src.add_argument("--file", help="read the message from a file")
    ap.add_argument("--send", action="store_true",
                    help="actually send (without this it is a dry run)")
    ap.add_argument("--check", action="store_true",
                    help="report whether the session is logged in, then stop")
    ap.add_argument("--login", action="store_true",
                    help="open WhatsApp Web and wait for you to scan the QR (one-off)")
    ap.add_argument("--marker", help="skip if this string is already in the recent messages")
    ap.add_argument("--chat", default=DEFAULT_CHAT)
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    ap.add_argument("--session", default=DEFAULT_SESSION)
    ap.add_argument("--executable", default=None,
                    help="Chrome binary (default: the installed Google Chrome)")
    a = ap.parse_args()
    kw = {"profile": a.profile, "session": a.session, "executable": a.executable}

    try:
        if a.login:
            return login(**kw)
        if a.check:
            open_self_chat(chat=a.chat, **kw)
            print("OK - logged in, self-chat reachable.")
            return 0
        text = a.text
        if a.file:
            with open(a.file, encoding="utf-8") as f:
                text = f.read()
        if not text:
            ap.error("one of --text, --file or --check is required")
        send(text, dry_run=not a.send, chat=a.chat, marker=a.marker, **kw)
        return 0
    except WhatsAppError as e:
        print("WhatsApp send failed: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
