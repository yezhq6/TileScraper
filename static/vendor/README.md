# 前端第三方库（本地内置）

`templates/index.html` 只引用本目录下的文件，**不依赖任何 CDN**：

* 页面在离线/内网（例如只有局域网瓦片服务）时也能正常打开；
* 避免 CDN 被投毒后，第三方 JS 以同源身份直接调用本站 `/api/*`。

升级方式：下载同路径的新版本文件覆盖，并更新下表的 sha384（`openssl dgst -sha384 -binary <file> | openssl base64 -A`）。

| 文件 | 来源 | 许可证 | 大小 | sha384 |
|---|---|---|---:|---|
| `bootstrap/bootstrap.min.css` | [Bootstrap 5.3.0 (CSS)](https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css) | MIT | 232914 | `sha384-f67742c946886f3022d855155c98b40a39826a94a63bb4a7a4979fd38f3aaa12e7b99d9c75e4613b4da2b8ae8551454c` |
| `leaflet-draw/images/spritesheet-2x.png` | [Leaflet.draw 1.0.4 (工具图标 @2x)](https://unpkg.com/leaflet-draw@1.0.4/dist/images/spritesheet-2x.png) | MIT | 3581 | `sha384-c53ae605a358860e7cbc0b49bf61e77e52f6d18a3858bdcd8d84ab7e9f20c79655cfe0777f261d7c82cffec9afbaf135` |
| `leaflet-draw/images/spritesheet.png` | [Leaflet.draw 1.0.4 (工具图标)](https://unpkg.com/leaflet-draw@1.0.4/dist/images/spritesheet.png) | MIT | 1906 | `sha384-19ed66200be0f0f3ef5406c390b564577bf3749f3279d43e0cfdd32b2b2d35eccab9f15c4cadcd8e32706d304578a658` |
| `leaflet-draw/images/spritesheet.svg` | [Leaflet.draw 1.0.4 (工具图标 SVG)](https://unpkg.com/leaflet-draw@1.0.4/dist/images/spritesheet.svg) | MIT | 5551 | `sha384-bc689404f813c5b41ccd82c125bcc2e313b370a90d535cd5c4f8871a02d978190770d22f89c315de119bdafa9e6606c6` |
| `leaflet-draw/leaflet.draw.css` | [Leaflet.draw 1.0.4 (CSS)](https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css) | MIT | 5267 | `sha384-3592e456e051304781e15799cf6ed6c1346f96179cdf46e243c5f1ef31bb2499e4bc4291839aa2e8135b117a3dc9dc2f` |
| `leaflet-draw/leaflet.draw.js` | [Leaflet.draw 1.0.4 (JS)](https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js) | MIT | 67484 | `sha384-24fe543f120ed939b6a3bf456f4b4660c6b8e239166abe77681a026ddf1a874f8b7020e8a214c862bfb3217c9f18824d` |
| `leaflet/images/layers-2x.png` | [Leaflet 1.9.4 (图层控件图标 @2x)](https://unpkg.com/leaflet@1.9.4/dist/images/layers-2x.png) | BSD-2-Clause | 1259 | `sha384-f85d9958afc74e9915f643761e730609039333f7274092ecefbd052ce787ce7c155917c311223ccf8270706626cb602e` |
| `leaflet/images/layers.png` | [Leaflet 1.9.4 (图层控件图标)](https://unpkg.com/leaflet@1.9.4/dist/images/layers.png) | BSD-2-Clause | 696 | `sha384-f34c7ce594be1b5f3da34c4bf04f03ec19df86e36cb3a13050f1f31bb7bea81c910f6c67a718a467a51053845ee67372` |
| `leaflet/images/marker-icon-2x.png` | [Leaflet 1.9.4 (默认标记图标 @2x)](https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png) | BSD-2-Clause | 2464 | `sha384-6c311ad5184000a22bfd5427319ee0521857c2629807857483c02cc4ebc210fc06c5f1c2504cc010c004133923bb1880` |
| `leaflet/images/marker-icon.png` | [Leaflet 1.9.4 (默认标记图标)](https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png) | BSD-2-Clause | 1466 | `sha384-c20f377c23978c1b6acc50168532fd49df6f98b50d85f104cdf98d517f73c2fda280a973fd841b75aa45e0e6ddc45f91` |
| `leaflet/images/marker-shadow.png` | [Leaflet 1.9.4 (标记阴影)](https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png) | BSD-2-Clause | 618 | `sha384-741f22bdfbcf19bd4c488cd7f285936a40b19aaf95c2a3ff40bd535f88d3e08351de93394f81601771b1e2637735332a` |
| `leaflet/leaflet.css` | [Leaflet 1.9.4 (CSS)](https://unpkg.com/leaflet@1.9.4/dist/leaflet.css) | BSD-2-Clause | 14806 | `sha384-b072fd3406fb94deeb7ef1b995f1e99bae375e4723ce9e2316fb9abc63a7767ea98d5a92ea7cb9e8202dde7b04553e07` |
| `leaflet/leaflet.js` | [Leaflet 1.9.4 (JS)](https://unpkg.com/leaflet@1.9.4/dist/leaflet.js) | BSD-2-Clause | 147552 | `sha384-73138f8edeecec8cf4e2e68725c781992faaa63bf626420735572e3ab33e607c193a6246057234d267545c4abae474c7` |

各文件保留了上游的版权/许可证头。上游项目：

* Bootstrap — https://github.com/twbs/bootstrap (MIT)
* Leaflet — https://github.com/Leaflet/Leaflet (BSD-2-Clause)
* Leaflet.draw — https://github.com/Leaflet/Leaflet.draw (MIT)

> 未内置 `bootstrap.bundle.min.js`：页面只用 Bootstrap 的 CSS，没有任何组件 JS 调用（无 `data-bs-*`、无 `new bootstrap.*`）。

> 注意：这些文件**保持与上游逐字节一致**（LF 行尾）——不要对它们做行尾转换
> （CRLF/LF），否则上表的 sha384 会变化，也就无法再与上游官方校验和对照。
