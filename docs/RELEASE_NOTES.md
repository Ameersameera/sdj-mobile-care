## SDJ Mobile Care – Smart Shop Management System

Offline shop software for Windows 10 / 11 (64-bit) and Android: billing, quick sale, stock, purchases, repair jobs,
customers & dues, expenses, returns, daily closing, reports and accounting.

### What's new in 1.4.0 – backups that survive a dead PC
- **Backup to the owner's phone** – after every *Close Day* the PC makes a locked backup (data + shop logo + closing
  report) and sends it to the linked phone over the shop Wi-Fi at the next sync. The phone saves it in
  **Downloads → SDJ Backups** – it stays even if the app is removed. Last 30 days + one per month are kept
  (or *Keep every backup*). Choose which phones keep backups in *Settings → Phone link*.
- **2nd backup folder** – pen drive or a *Google Drive for desktop* folder; every backup is copied there too.
- **Google Drive from the phone** (optional) – choose a Drive file once; each new backup is copied into it
  (*Only on Wi-Fi* option, "Last copied" shown).
- **Locked with the owner's password** (AES-256) + a printable **recovery code** for a forgotten password.
  Every backup is checked right after it is made.
- **New PC** – the first screen offers **Restore from phone** (QR), **Restore from a backup file** (pen drive / Drive
  download) or **Start fresh**. Linked phones keep working; entries they made after the backup are sent again
  automatically (each one is still saved only once).
- **Emergency** – no backup anywhere? The phone can **rebuild the new PC** from its own copy: products, stock, IMEIs,
  customers with dues, open repair jobs and the logins.
- Dashboard shows where the last backup is; **Backup late** warning after 2 days (phone) / 7 days (2nd folder).

### Download
- **SDJMobileCare_Setup_1.4.0.exe** – Windows installer (recommended)
- **SDJMobileCare-Windows-app-v1.4.0.zip** – portable Windows version (extract and run `SDJMobileCare.exe`)
- **SDJMobileCare-Phone-1.4.0.apk** – Android phone app (Android 8 or newer)

### PC + phone update pannradhu eppadi
1. PC: `SDJMobileCare_Setup_1.4.0.exe` download panni run pannunga (app-ai close pannitu). Data appadiye irukkum.
2. Phone: `SDJMobileCare-Phone-1.4.0.apk` install pannunga (pazhaya app mela update aagum, link-um data-vum irukkum).

### Backup setup – once (5 nimisham)
1. PC: **Settings → Backup → Create recovery code…** → password → code-ai **Print** panni safe-aa vechukonga
   (PC pakkathula vekkaadheenga).
2. PC: **Choose 2nd backup folder…** → pen drive (illa *Google Drive for desktop* irundhaa `G:\My Drive\SDJ Backups`).
3. Phone (owner): app-la **More → Backups** → **Choose Google Drive file** → ☰ → Google Drive → **Save** (optional).
4. Daily **Close Day** pannunga – backup automatic-aa phone-ukku (kadai Wi-Fi-la app open pannum bodhu) + pen drive-ku pogum.
5. Dashboard-la "Last backup: phone … · 2nd folder …" paarunga. **Backup late** vandhaa phone app-ai Wi-Fi-la open pannunga /
   pen drive connect pannunga.

### PC crash aagi pudhu PC-la restore pannradhu eppadi
1. Pudhu PC-la `SDJMobileCare_Setup_1.4.0.exe` install pannunga.
2. First screen: **Restore from phone** click pannunga – QR code varum.
3. Phone: kadai Wi-Fi-la connect → app → **More → Backups** → latest backup-la **Send to PC** → **Scan the PC's QR**.
4. PC: owner password (illa recovery code) potu **Restore this backup** → sign in. Mudinjadhu – phone-um appadiye work aagum.
   - Pen drive / Google Drive file irundhaa: **Restore from a backup file** → `.sdjb` file choose → password.
   - Phone-um pudhusaa? App install → link screen-la **"New PC for the shop? Send it a backup file"** → **Add a backup file**
     (Drive-la irundhu) → **Send to PC**.
5. **Backup edhuvume illa-na (emergency)**: PC-la **Restore from phone** → phone app-la (owner login)
   **Backups → Rebuild a new PC from this phone** → QR scan → PC-la owner password → **Rebuild this PC from the phone**.
   Products, stock, IMEI, customers dues, open repairs, logins varum. Pazhaya bills, purchases, expenses, cost price varaadhu.

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

### Phone app first time install + link pannradhu eppadi
1. Phone-la `SDJMobileCare-Phone-1.4.0.apk` download pannunga (Chrome → Download → open).
2. "Install unknown apps" kettaa → **Settings** → **Allow from this source** → back → **Install**.
3. Phone-ai kadai Wi-Fi-la connect pannunga. PC-la SDJ Mobile Care open-aa irukkanum.
4. PC: **Settings → Phone link** → password → account choose panni **Create one-time code**.
5. Phone app: **Scan QR from PC** (illa code type pannunga) → link aanadhum account password potu **Sign in**.
6. Phone "Could not reach the PC" sonnaa: PC-la **Settings → Phone link → Allow phone sync in Windows Firewall** click pannunga.

Phone tholanjaa: PC **Settings → Phone link → Remove phone**. Phone sync uses no cloud and no internet – data stays on
the shop PC and your phones. Google Drive is only an optional extra place for backups.
