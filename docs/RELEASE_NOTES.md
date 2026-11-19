## SDJ Mobile Care – Smart Shop Management System

Offline shop software for Windows 10 / 11 (64-bit) and Android: billing, quick sale, stock, purchases, repair jobs,
customers & dues, expenses, returns, daily closing, reports and accounting.

### What's new in 1.3.1 – phone link fixes
- **"Could not reach the PC" fix** – *Settings → Phone link* now shows whether Windows Firewall allows phone sync and has a
  one-click **Allow phone sync in Windows Firewall** button (Windows asks permission once). It also removes old
  "blocked" entries for SDJ. Creating a link code offers it automatically when the rule is missing.
- The installer now allows the sync ports too (TCP 47810, UDP 47811), so the portable copy works as well.
- **Connection test** – open `http://<PC address>:47810` in the phone's browser; it says
  *"SDJ Mobile Care phone sync is working"* when the network is fine.
- **Phone app** – clearer messages (firewall / PC app not open / router blocks devices), a **Test the connection** button,
  and it now tries every Wi-Fi route of the phone (phones with dual Wi-Fi or "use mobile data when Wi-Fi has no internet").

### What's new in 1.3.0 – Phone app + Phone link (no cloud)
- **Android phone app** – same shop data on the phone: dashboard, bills (camera barcode / IMEI scan), edit / cancel
  bill, share the bill as PDF (WhatsApp), stock, repair jobs (new job, status, spare parts, payment, delivery),
  expenses and customer dues. Sign in with the same username and password as the PC.
- **Works offline** – the phone keeps its own copy. Entries made with no Wi-Fi (or with the PC switched off) are
  saved on the phone and go to the PC automatically when the phone is back on the shop Wi-Fi. Mobile data is never used.
- **Phone link** – *Settings → Phone link* (owner password): one-time code / QR to link a phone, list of linked phones,
  **Remove phone** for a lost phone, and "Start SDJ Mobile Care when Windows starts".
- **No clashes** – phone bills have their own number series (SDJ-P1/…, JOB-P1-…). Stock is counted from every sale,
  so sales on both sides add up. Each entry is saved exactly once, even if the Wi-Fi drops in the middle of a sync.
- **Sync alerts** – the few cases that need the owner: last piece sold on both, the same bill changed on both
  (choose phone or PC version), entries for a day that was already closed (**Update closing**).
- Sync is encrypted with each phone's own key. A safety backup is taken before phone entries are merged.

### Download
- **SDJMobileCare_Setup_1.3.1.exe** – Windows installer (recommended)
- **SDJMobileCare-Windows-app-v1.3.1.zip** – portable Windows version (extract and run `SDJMobileCare.exe`)
- **SDJMobileCare-Phone-1.3.1.apk** – Android phone app (Android 8 or newer)

### PC install / update pannradhu eppadi
1. `SDJMobileCare_Setup_1.3.1.exe` download panni double-click pannunga.
2. "Windows protected your PC" vandhaa → **More info** → **Run anyway**.
3. **Install for all users** → **Next** → **Install** → **Finish**.
4. Pazhaya version irundhaa, app-ai close panni idhe setup-ai run pannunga – unga data appadiye irukkum.

### Phone app install + link pannradhu eppadi
1. Phone-la `SDJMobileCare-Phone-1.3.1.apk` download pannunga (Chrome → Download → open).
2. "Install unknown apps" kettaa → **Settings** → **Allow from this source** → back → **Install**.
3. Phone-ai kadai Wi-Fi-la connect pannunga. PC-la SDJ Mobile Care open-aa irukkanum.
4. PC: **Settings → Phone link** → password → account choose panni **Create one-time code**.
5. Phone app: **Scan QR from PC** (illa code type pannunga) → link aanadhum account password potu **Sign in**.
6. Phone "Could not reach the PC" sonnaa: PC-la **Settings → Phone link → Allow phone sync in Windows Firewall** click pannunga.

Phone tholanjaa: PC **Settings → Phone link → Remove phone**. All data stays on the shop PC and your phones –
no cloud, no internet.
