# Phone app signing key

`sdj-release.jks.enc` is the app signing key, encrypted (AES-256, PBKDF2 600k).
The passphrase is NOT in the repo. It lives in the GitHub secret `SDJ_KEYSTORE_PASSPHRASE`
(and the same text is the keystore + key password, alias `sdj`).

CI decrypts it at build time. Every phone update must be signed with this same key,
so keep the passphrase safe: without it the key cannot be opened.
