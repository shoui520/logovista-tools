# Android Packages

LogoVista Android packages currently appear in two distinct forms.

## Android SSED

Android SSED packages keep the classic SSED component model but may use Android
wrapper names:

- the catalog magic can be `LVEDINFO` instead of `SSEDINFO`;
- the body component can be `HONMON.DIW` instead of `HONMON.DIC`;
- `HONMON.DIW` decrypts to an SSED `HONMON` stream wrapped as
  `LV_` + `SSEDDATA`, with a two-byte alignment pad before the chunk-offset
  table;
- companion plaintext SQLite files such as `DICT.db` and
  `DICT_indexinfo.db` are Android UI/body metadata sidecars, not
  `LVED_SQLITE3` payloads.

The observed `HONMON.DIW` cipher is the Android viewer path:

```text
AES/CBC/PKCS5Padding
PBEWITHSHAAND256BITAES-CBC-BC key derivation
password: resworbncidnatsivogol--emulator
iterations: 1024
```

The toolkit reports this body storage as `android_honmon_diw`.

## Android LVED_SQLITE3

Other Android packages are modern `LVED_SQLITE3` packages. They are hidden
package directories whose primary payload is a same-name `DICT.db` SQLCipher
database. They include `resource/conf.ini` or `resource/property.data`, and the
viewer `dictinfo.xml` supplies:

- numeric dictionary id;
- dictionary code;
- display title;
- Android runtime font names.

The SQLCipher key derivation matches the desktop LVED metadata path:

```text
key_code = (dict_code[0] + dict_code[-1]).lower()
key = "jlasgoi" + "ahoiam" + "pvsjhosD" + "Hfopj" + key_code + str(dict_id * 19286)
```

Reports never emit the derived key. The Android font names are external viewer
runtime dependencies, not missing dictionary resources.

## Reader Implications

Android is not a single package family. A reader core should route by detected
container and payload:

- Android SSED: shared SSED catalog/index/HONMON/gaiji/resource providers, with
  `LVEDINFO` and `HONMON.DIW` support;
- Android LVED_SQLITE3: shared LVED SQLite providers, with Android
  `dictinfo.xml` metadata as the key/title source;
- Android plaintext helper DBs: schema-classified sidecars, not primary
  SQLCipher books.
