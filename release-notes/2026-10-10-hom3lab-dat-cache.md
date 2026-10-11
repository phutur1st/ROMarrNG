---
category: fixed
audience: users, operators
area: catalog
action: none
breaking: false
---
DAT browsing, searches and request identity checks now share a single in-memory catalog snapshot. Reloading DATs or changing preferred regions rebuilds it; concurrent requests build once and returned JSON cannot alter the snapshot. Page serialization is limited to the requested page, and detail lookup uses a key index. Library imports wake the background listing refresh, and Seerr library matching uses canonical platform slugs when provided by the backend. Missing-on-disk and remote catalog rows no longer count as available ROMs.
