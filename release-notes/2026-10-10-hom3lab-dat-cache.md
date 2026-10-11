---
category: fixed
audience: users, operators
area: catalog
action: none
breaking: false
---
DAT browse, search and detail now reuse one catalog snapshot, rebuilding on reload or region changes. Concurrent reads build once. ROM imports wake the library refresh; Seerr matches canonical platform slugs and no longer marks missing-on-disk catalog entries as available.
