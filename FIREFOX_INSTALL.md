# Installing the Magic Downloader extension permanently in Firefox

## The one rule you can't get around

**Regular Firefox (Release/Beta) will not permanently install an unsigned
add-on, and the "allow unsigned" switch is disabled in those builds.** A permanently-installable
Firefox add-on has to be *signed* and published on
[addons.mozilla.org](https://addons.mozilla.org/).

So you have exactly two ways to keep this extension installed permanently:

- **No account, no signing** → use a Firefox build that lets you turn signing
  off (**Option A** below). This is what you want.
- **Regular Firefox** → it must be signed (Option B).

Build the Firefox package (`python build_extension.py` from the project root):

```
E:\PycharmProjects\Magic_downloader\magic_downloader_firefox.zip   (install this / AMO upload)
```

Rename it to `.xpi` if a step below asks for one — Firefox accepts either.

---

## ✅ Option A — Permanent, NO sign-in, NO signing (recommended for you)

Regular Firefox blocks this, but these free official builds allow it:
**Firefox Developer Edition, Nightly, or ESR.** No Mozilla account, nothing
uploaded anywhere.

1. Install one of:
   - [Firefox Developer Edition](https://www.mozilla.org/firefox/developer/) (easiest), or
   - [Firefox Nightly](https://www.mozilla.org/firefox/channel/desktop/#nightly), or
   - [Firefox ESR](https://www.mozilla.org/firefox/enterprise/).
2. Open `about:config`, accept the warning, search for
   **`xpinstall.signatures.required`**, and set it to **`false`**
   (double-click to flip it).
3. Open `about:addons` → gear ⚙ → **Install Add-on From File…** → choose
   **`magic_downloader_firefox.zip`**.
4. Confirm **Add**. It now stays installed across restarts — permanently, unsigned.
5. Grant site access (see the end of this file).

> Why not regular Firefox? Mozilla locked `xpinstall.signatures.required` to
> `true` in Release/Beta specifically so unsigned add-ons can't persist. Only
> the builds above honour the `false` setting. It's a Firefox policy, not
> something the extension can override.

### Prefer to stay unsigned AND on your normal browser?

Then use **Chrome / Edge / Brave** instead of Firefox. Their **Load unpacked**
(from `chrome://extensions`, Developer mode) keeps the extension installed
permanently with **no signing and no account** — that's the simplest
"stays in my browser without signing in" option of all.

---

## Option B — Permanent in ANY (regular) Firefox — needs Mozilla to sign it (free)

You upload the zip, Mozilla signs it (unlisted = instant, no review), you get a
`.xpi` that installs permanently in normal Firefox — the proper way.

Only needed if you insist on staying on **regular** Firefox. Mozilla signs it
free (unlisted = instant, no public review), giving you a `.xpi` that installs
permanently — but it does require a free account.

### B1. Mozilla's web tool (no command line)

1. Create a free account and open the
   **[Developer Hub → Submit a New Add-on](https://addons.mozilla.org/developers/addon/submit/distribution)**.
2. Choose **“On your own”** (self / unlisted distribution) — *not* "On this site".
3. Upload `magic_downloader_firefox.zip` (build it with `python build_extension.py`).
4. Mozilla validates and **signs it automatically** (seconds to a couple minutes).
5. **Download the signed `.xpi`.**
6. `about:addons` → gear ⚙ → **Install Add-on From File…** → pick the signed
   `.xpi`. It now survives restarts.

### B2. Command line (`web-ext`) — good for re-signing on every update

```powershell
npm install -g web-ext        # one-time (needs Node.js)

# Get API credentials once from:
#   https://addons.mozilla.org/developers/addon/api/key/
cd E:\PycharmProjects\Magic_downloader
node build.js --firefox        # put the Firefox manifest in browser_extension\
cd browser_extension
web-ext sign --channel=unlisted --api-key=YOUR_JWT_ISSUER --api-secret=YOUR_JWT_SECRET
```

`web-ext` uploads, waits for the signature, and drops the signed `.xpi` in
`web-ext-artifacts\`.

---

## Option C — Temporary (any Firefox, wiped when Firefox closes)

For a quick test only:

1. Run `node build.js --firefox` (writes the Firefox `browser_extension\manifest.json`).
2. `about:debugging#/runtime/this-firefox`
3. **Load Temporary Add-on…** → select
   `browser_extension\manifest.json`.

---

## After installing (all options)

Firefox MV3 makes site access **opt-in**. Open the toolbar extensions menu →
**Magic Downloader** → **Permissions** → allow **“Access your data for all
websites.”** The video sniffing and the download button won't work until this
is granted. Also keep the desktop app running (Browser API on `127.0.0.1:7373`).
