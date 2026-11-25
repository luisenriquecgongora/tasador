# Changelog

## [0.4.0](https://github.com/luisenriquecgongora/tasador/compare/v0.3.0...v0.4.0) (2026-11-25)


### Features

* **api:** expose PDF support in /v1/identify and add automated tests ([4d87701](https://github.com/luisenriquecgongora/tasador/commit/4d87701d1d868cbdcab6db6911719bc4d1a33517))
* **desktop:** add live pipeline inspector and execution history ([14ea1a5](https://github.com/luisenriquecgongora/tasador/commit/14ea1a565679e8a02a8a8cffb48344f3d04e56f0))
* **desktop:** auto-adopt running engine and clarify search status ([36dff2c](https://github.com/luisenriquecgongora/tasador/commit/36dff2c8f5c9edb658fc2fb07db86363bf6e5df6))
* **engine:** add activity tracking and rerun endpoints ([76a7c4d](https://github.com/luisenriquecgongora/tasador/commit/76a7c4da4a0d4b0edcd6898bf1a310ac14490156))
* **engine:** add PDF parsing and rasterization pipeline via pypdfium2 ([2ef23fb](https://github.com/luisenriquecgongora/tasador/commit/2ef23fb1dbe88a749b00128c8b22d6c1fa2b2ba7))
* **pipeline:** add document OCR fallback heuristics for name brand and price ([0ce845a](https://github.com/luisenriquecgongora/tasador/commit/0ce845a3d302dc61f171cbc5495627bbdf83c64a))
* sqlite activity persistence and desktop window sizing for split studio ([41c7111](https://github.com/luisenriquecgongora/tasador/commit/41c7111243dd1aadff3bff696b0d69e49a3d031c))
* **studio:** add architecture and interactive pipeline DAG tab ([b3cdb44](https://github.com/luisenriquecgongora/tasador/commit/b3cdb442cd43bf8a0f1446f1b69147761e87c050))
* **studio:** side-by-side split layout for pipeline inspector and document viewer ([4b7c1c5](https://github.com/luisenriquecgongora/tasador/commit/4b7c1c5bff3fe49a406b5bb40408016c3517461b))
* **ui:** add model hub hardware sizing and api key visibility toggle ([fdd8568](https://github.com/luisenriquecgongora/tasador/commit/fdd8568cfe662cd89cdc141a5906aee0e45309e1))


### Bug Fixes

* **cors:** allow all headers for browser preflight and isolate test db ([0099606](https://github.com/luisenriquecgongora/tasador/commit/009960650199a768444a72bcaa221fca139d2d13))
* **desktop:** integrate single-instance plugin and prevent webview lock crashes ([bfca43d](https://github.com/luisenriquecgongora/tasador/commit/bfca43d421229fc52c2013421c631976658a7a60))
* **pipeline:** add live step tracking and fix status transition ([5b64d11](https://github.com/luisenriquecgongora/tasador/commit/5b64d11557ba7cfcaff5ed34e11e49ae8f0977b8))
* **studio:** fix event listener binding in el helper so Inspect Trace works ([1f6d570](https://github.com/luisenriquecgongora/tasador/commit/1f6d5701d175b4646dccae372e5bfc65bcc53faf))

## [0.3.0](https://github.com/luisenriquecgongora/tasador/compare/v0.2.0...v0.3.0) (2026-11-23)


### Features

* **scripts:** add visual diff, artifact opener, and cowork timeline ([c9a2550](https://github.com/luisenriquecgongora/tasador/commit/c9a25503a8b33a217d8916b9772418143bac1a6c))
* **scripts:** sync cowork vision hud and visual release digest v1.4.5 ([64a383c](https://github.com/luisenriquecgongora/tasador/commit/64a383ca319803adcdde2511c745036729c71e54))

## [0.2.0](https://github.com/luisenriquecgongora/tasador/compare/v0.1.0...v0.2.0) (2026-11-13)


### Features

* **deploy:** build the installer on macOS ([e914715](https://github.com/luisenriquecgongora/tasador/commit/e914715f3a746e4ebfc35ede927407784dd9fb2b))
* **desktop:** add the macOS app icon ([db97a79](https://github.com/luisenriquecgongora/tasador/commit/db97a792d2e39fa4270d354f1bb225cdb8899454))


### Bug Fixes

* **desktop:** find docker when opened from Finder ([23fc4dc](https://github.com/luisenriquecgongora/tasador/commit/23fc4dcb60d2074d66c86353555f06a24ee7f255))

## 0.1.0 (2026-11-13)


### Features

* **config:** add desktop launcher and shared port ([921ee81](https://github.com/luisenriquecgongora/tasador/commit/921ee81deb6f1a522af84befad01890ef9a878ff))
* **config:** add Windows launcher and settings example ([cf2fe47](https://github.com/luisenriquecgongora/tasador/commit/cf2fe472e138cc63742f47621bd838414fc672a6))
* **config:** make the Windows launcher one click ([3b8a1df](https://github.com/luisenriquecgongora/tasador/commit/3b8a1df430ec771991703500c30af4c5478b4f12))
* **config:** make vision and search providers generic ([670c673](https://github.com/luisenriquecgongora/tasador/commit/670c673cdb622e82b7d3a35fbe302dcac28ae9e4))
* **config:** open the settings page from the launcher ([a537c08](https://github.com/luisenriquecgongora/tasador/commit/a537c08326aa60c80f7774d463f856585534034d))
* **desktop:** add Tauri app around the engine ([2ac0ce4](https://github.com/luisenriquecgongora/tasador/commit/2ac0ce42698d2601c5ae83c4476a1b1bff51153a))
* **desktop:** add the app window page ([bd60b9f](https://github.com/luisenriquecgongora/tasador/commit/bd60b9fdc250bff15aabe4ae1919cc8cc82ee047))
* **desktop:** run the bundled engine when installed ([f14ee69](https://github.com/luisenriquecgongora/tasador/commit/f14ee69a39197e0179dd946c7fccd483dee1f925))
* **engine:** add a local settings page ([22219ee](https://github.com/luisenriquecgongora/tasador/commit/22219eefd61a2ccd1e9737df10d93ea96c0ad201))
* **engine:** add a standalone entry point ([027644a](https://github.com/luisenriquecgongora/tasador/commit/027644a71068fa6def75238362890dfb6e09140c))
* **engine:** add identify pipeline and HTTP API ([36fb041](https://github.com/luisenriquecgongora/tasador/commit/36fb041d1224138fd1e959922fc9ec36af7d53b2))
* **engine:** add local readers, pricing and product key ([a0773a8](https://github.com/luisenriquecgongora/tasador/commit/a0773a85a625385b5059e09a1fd51d8d2e806e1b))
* **engine:** add settings, contract and photo intake ([a3a555d](https://github.com/luisenriquecgongora/tasador/commit/a3a555d7ec6827a7104262e5a94a35477462c403))
* **engine:** explain local search in the settings page ([58bb439](https://github.com/luisenriquecgongora/tasador/commit/58bb4396a1dd37c7ac602f86780ee72b6da78302))
* **engine:** load .env and detect local services ([a76d507](https://github.com/luisenriquecgongora/tasador/commit/a76d507a55a5d8661916a99559e5fe2c02c82047))
* **search:** add web search, link sorting and page checks ([4711302](https://github.com/luisenriquecgongora/tasador/commit/4711302490f9b8945fa6a048d9bac10320d3da0f))
* **search:** learn the barcode number from shop pages ([7bae515](https://github.com/luisenriquecgongora/tasador/commit/7bae515980629897cb0b9bb80e96e05790d9732d))
* **vision:** add Claude and local vision providers ([e0a5744](https://github.com/luisenriquecgongora/tasador/commit/e0a57445bcb73589c621f9acf290163728293104))


### Bug Fixes

* **config:** start releases at 0.1.0 ([4692a3e](https://github.com/luisenriquecgongora/tasador/commit/4692a3eb8bb05d922e8bc8d95c6b73ad95429a8c))
* **deploy:** give SearXNG engines more time ([36b5018](https://github.com/luisenriquecgongora/tasador/commit/36b50183fe3420511282d3d4d5f309904d4a5dd2))
* **engine:** trust the OS certificate store for HTTPS ([f5f1637](https://github.com/luisenriquecgongora/tasador/commit/f5f16373998e309e6f6f767deecaa7e1b084bf23))
* **search:** keep search pages as links, drop other models ([25428bd](https://github.com/luisenriquecgongora/tasador/commit/25428bdff1f1190f01632023a432c84ae9e4b631))
