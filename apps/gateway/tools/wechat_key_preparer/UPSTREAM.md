# wcdb-key-tool provenance

- Upstream: https://github.com/TANGandXUE/wcdb-key-tool
- Pinned commit: `79f1b5b92e12c66aa281b4a60a3c478b5f547dfa`
- Upstream file: `wcdb_key_tool_windows.py`
- Upstream file SHA-256: `9de22ccc5070bfc5325ece1bdf4da8ca537eea594b821fc2acd46a62c0e41958`
- License: MIT; preserved in `LICENSE.wcdb-key-tool`
- License SHA-256: `098659c726e64afd1c503227c1f13866a3ae55a533744185d953828f37c54d8e`

The upstream file is vendored byte-for-byte. Chatbox does not modify it. The local
`core.py` adapter invokes only the Windows read-only runtime scanner, suppresses
console output, captures only HMAC-verified entries in memory, removes upstream
path metadata, and writes one Chatbox-compatible `all_keys.json`. The standalone
preparer contains no network client and does not run inside the Chatbox process.

Upstream decrypt, passphrase-cache, and experimental debugger commands remain
present only because the pinned source is preserved for auditability; the Chatbox
preparer does not expose or call them.
