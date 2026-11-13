# 原版 UI 字符串 / tips / update.zip 模板清单（重建版复用依据）

> 数据来源（全部为从原版静态提取，未运行原程序）：
> - `analysis/resources/strings/LogoBuilder.Strings.resources.txt` —— 中性（英文，`NeutralResourcesLanguage("en")`）业务提示串
> - `analysis/resources/strings/LogoBuilder.Strings.zh-Hans.resources.txt` —— 简体中文业务提示串
> - `analysis/resources/strings/LogoBuilder.MainForm.zh-Hans.resources.txt` / `LogoBuilder.SignControl.zh-Hans.resources.txt` / `LogoBuilder.SizeDialog.zh-Hans.resources.txt` —— 窗体本地化文案
> - `analysis/resources/strings/Tips.TipsForm.resources.txt` / `Tips.TipsForm.zh-Hans.resources.txt` —— Tips 窗口文案
> - `analysis/resources/ra_*.resources`（manifest 文件见 `analysis/resources/ui_assets/*/manifest.txt`）—— 英文窗体文案/图片
> - `LogoBuilder_jb51/LogoBuilder_v1.6中文版/tips.txt` 及 `es|it|pt|ru|zh-Hans/tips.txt`
> - `analysis/resources/template_*`、`ra_LogoBuilder.update.zip`
> 其它语言（es/it/pt/ru）的 `LogoBuilder.resources.dll` 未逐条提取；结构同 zh-Hans 卫星程序集。

---

## 1. 业务提示串 `LogoBuilder.Strings`（17 键，`Strings.resx`）

| Key | English（中性资源原文） | zh-Hans（原版译文，逐字） | 建议译文（重建版，尽量沿用原版） | 备注 |
|---|---|---|---|---|
| errorPacking | `Error packing new logo.bin\r\n{0}` | `打包新logo.bin错误\r\n{0}` | 复用中文原文 | {0}=异常信息 |
| noImages | `No images found.\r\nNote that images numbers must start from 1.` | `没有发现图片文件。\r\n需要注意的是图像的数字，必须从1开始。` | 复用中文；建议追加"项目目录缺少 imgN.png / .project 未定义图像块" | 打包空项目 |
| dataLengthError1 | `Error in data length (1st case)` | `数据长度错误(第一种情况)` | **不建议复用**：改为"文件长度与头部 dataLen 不符（dataLen=..., 实际可用=...）" | B8 |
| dataLengthError2 | `Error in data length (2nd case)` | `数据长度错误(第二种情况)` | 同上，区分为"payload 内两个 dataLen 不一致" | B8 |
| imageSizeError | `Image size doesn't correspond to settings {0}.\r\nYou must fix this error or delete the image file.` | `图像尺寸和设置值不对应{0}。\r\n你必须修复该错误或删除的图像文件。` | 复用中文（原版实际未调用，重建版应在打包前真正使用）| 死字符串，见 N9 |
| cantReadProjectFile | `Cant read .project file` | `无法读取.project文件` | 复用中文，补路径 | — |
| errorCreatingBootLogo | `Can't create boot_logo file\r\n{0}` | `不能创建第二屏文件{0}` | 复用中文；英文原版就是 `\n` 而非 `\r\n`（不影响）| — |
| selectExistingProjectFolder | `Select existing project folder` | `选择已有项目的工作目录` | 复用中文 | 文件夹对话框标题 |
| error | `Error` | `错误` | 复用 | 通用错误标题 |
| refreshImages | `Refresh images` | `刷新图像` | 复用（原版未直接用？）| 资源键保留 |
| unexpectedBufferSize | `Unexpected data size` | `意外的数据大小` | 改为可读信息（期望 vs 实际） | B8 |
| cancelled | `Cancelled by user` | `被用户取消` | 复用 | 取消类异常文案 |
| cantOpenProject | `Can't open project\r\n{0}` | `无法新建项目\r\n{0}` ← **原版复制粘贴错误，应为"无法打开项目"** | 改为"无法打开项目\r\n{0}" | 纠错点 |
| errorCreatingUpdate | `Error creating update.zip\r\n{0}` | `创建update.zip失败\r\n{0}` | 复用中文 | — |
| takeIt | `Take your update.zip in {0} :)` | `移动叔叔提醒你：创建卡刷补丁update.zip成功！ {0} :)` | 建议改为中性表述："卡刷包已生成：{0}"（重建版无"移动叔叔"品牌）| 品牌文案 |
| selectNewProjectFolder | `Select new project folder` | `选择新建项目的工作目录` | 复用中文 | 文件夹对话框标题 |
| cantCreateProject | `Can't create project\r\n{0}` | `无法新建项目\r\n{0}` | 复用中文 | — |

> ⚠ `takeIt`/`btnPack.ToolTip` 的"移动叔叔/忘记月亮"是 jb51 汉化版作者品牌，重建版应替换为中性文案，但可保留语义。

---

## 2. 主窗体 `MainForm` 文案（英文来自 `ra_LogoBuilder.MainForm.resources`，中文来自 zh-Hans 卫星资源）

| 资源键 | English | zh-Hans（原版） | 建议译文 | 控件 |
|---|---|---|---|---|
| `$this.Text` | `Logo Builder` | *(未本地化)* | `Logo Builder`（品牌名保留） | 主窗体标题；运行时被 `SetVersion` 覆盖为 `Logo Builder v. 1.6` |
| `btnCreate.Text` | `Create new project` | `制作新的logo.bin` ← 译文把"新建项目"译成了"做新 bin" | `新建项目` | 新建 |
| `btnCreate.ToolTip` | `Create new project from existing logo.bin file` | `分解现有的logo.bin文件，制作新的logo.bin文件` | 复用中文 | — |
| `btnOpen.Text` | `Open existing project` | `打开工作目录` ← **误译**（实际是打开已有项目） | `打开项目` | 打开已有项目 |
| `btnOpen.ToolTip` | `Open existing project` | `打开工作目录` ← 误译 | `打开已有项目` | — |
| `btnPack.Text` | `Make` | `开始生成` | `开始生成（logo.bin + update.zip）` 或保留"开始生成" | 底部打包按钮（有项目后显示）|
| `btnPack.ToolTip` | `Make new logo.bin and update.zip for CWM` | `忘记月亮提示：制作适用于移动叔叔recovery的update.zip或线刷用的logo.bin。` | `生成 logo.bin 与 CWM 卡刷包 update.zip` | — |
| `btnOpenFolder.ToolTip` | `Open project folder in windows explorer` | `使用window资源管理器打开工作目录` | `在文件管理器中打开项目目录` | 底部小按钮 |
| `btnCreateBootLogo.ToolTip` | `Create boot_logo file from image` | `选择图片创建开机第二屏` | 复用中文 | 顶部第二屏→图 |
| `btnOpenBootLogo.ToolTip` | `Create image from boot_logo file` | `从开机第二屏提取图片文件` | 复用中文 | 顶部图→第二屏 |
| `btnTips.ToolTip` | `Show tips` | `显示提示` | 复用 | Tips 按钮 |
| `btnWeb.ToolTip` | `Program in the web` | `打开官方主页` | `打开项目主页` | 主页按钮 |
| `ofdLogoBin.Filter` | `logo.bin\|logo.bin\|logo.img\|logo.img\|All files (*.*)\|*.*` | `logo.bin\|logo.bin\|logo.img\|logo.img\|所有文件 (*.*)\|*.*` | `MTK LOGO 镜像\|*.bin;*.img\|所有文件 (*.*)\|*.*` | 文件类型过滤器（原版写法会显示"logo.bin"而不是"*.bin"；建议修正）|
| `ofdImage.Filter` | `Image files\|*.bmp;*.jpg;*.png\|All files (*.*)\|*.*` | `图片文件\|*.bmp;*.jpg;*.png\|所有文件 (*.*)\|*.*` | 复用中文；可加 `*.gif;*.webp` | 选图 |
| `ofdBootLogo.Filter` | `boot_logo\|boot_logo\|All files (*.*)\|*.*` | `boot_logo\|boot_logo\|所有文件 (*.*)\|*.*` | `boot_logo 裸数据\|*.*` | 打开第二屏 |
| `fbdProject.Description` | —（英文未本地化该键）| `选择新的工作目录` | `选择项目目录` | 文件夹对话框 Description |

---

## 3. `SizeDialog` 文案

| 资源键 | English | zh-Hans | 建议译文 |
|---|---|---|---|
| `$this.Text` | `Image size` | `图像大小` | 复用中文 |
| `btnOk.Text` | `OK` | `确定` | 复用 |
| `btnCancel.Text` | `Cancel` | `取消` | 复用 |
| `lblSize.Text`（设计期占位） | `label1` | — | 运行时替换为 `{W} x {H} ({bits} bit)` |
| `cbColorDepths.Items[0]`（硬编码字符串池 1223） | `16 bit (MTK65xx)` | *未本地化（所有语言都显示英文）* | `16 位 RGB565（MTK65xx）` / 英文保留 |
| `cbColorDepths.Items[1]`（字符串池 1256） | `32 bit (MTK67xx)` | *未本地化* | `32 位 BGRA8888（MTK67xx）` |
| 运行时尺寸文字（字符串池 1289） | `{0} x {1} ({2} bit)` | 同上（格式串未本地化） | `{0} × {1}（{2} 位）` 或保留原格式 |

> 注意：`16 bit (MTK65xx)` 的 `xx` 在原版资源里就是字面 `MTK65__`（`__` 是 resx 里的占位？实际显示为 `MTK65__`）；重建版直接写 `MTK65xx`。

---

## 4. `SignControl`（作者/捐赠）

| 资源键 | English | zh-Hans | 说明 |
|---|---|---|---|
| `lblBy.Text`（硬编码 "by"）| `by` | `by`（未本地化）| 8.25pt |
| `lblEmail.Text`（硬编码）| `kadan` | `kadan` | 链接色 MediumBlue，点击发邮件 |
| `lblEmail.ToolTip` | `Email me (ankadnikov@gmail.com)` | `邮箱 (ankadnikov@gmail.com)` | — |
| `pictureBox1.ToolTip` | `Was the program helpful for You?` | `捐赠支持` | 点击打开 PayPal |
| `$this.ToolTip` | *(空)* | — | — |

- 邮件 URL（字符串池 3568）：`mailto:ankadnikov@gmail.com?subject=` + EmailTheme（原版未设置，subject 空）。
- 捐赠 URL（字符串池 3641 / 4077）：
  - 非 ru 语言：`https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=ankadnikov%40gmail.com&lc=RU&item_name=Program+support&no_note=0&currency_code=USD&bn=PP-DonationsBF%3Abtn_donate_SM.gif%3ANonHosted&submit.x=34&submit.y=6`
  - ru 语言：`https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=ankadnikov%40gmail.com&lc=RU&item_name=Anton%20Kadnikov&item_number=LogoBuilder&currency_code=RUB&bn=PP-DonationsBF:btn_donate_SM.gif:NonHosted`
- **重建版建议**：不复制作者品牌/捐赠链接；若要保留"关于"信息，用中性"原项目：LogoBuilder v1.6 by kadan (2012)，Crypto Obfuscator 保护，原版仅 Windows"。

---

## 5. `Tips` 窗口文案

| 资源键 | English | zh-Hans | 建议译文 |
|---|---|---|---|
| `lblTitle.Text` | `Did you know ...?` | `移动叔叔欢迎你！` | `使用提示` / `Did you know ...?` |
| `cbShowAtStartUp.Text` | `Show tips at startup` | `启动时显示提示` | 复用 |
| `btnPrev.Text` | `Previous tip` | `上一个提示` | 复用 |
| `btnNext.Text` | `Next tip` | `下一个提示` | 复用 |
| `btnClose.Text` | `Close` | `关闭` | 复用 |
| `$this.Name` | `TipsForm` | — | 窗口标题使用窗体 Text（资源里未覆盖，默认 `TipsForm`）|

窗口尺寸：ClientSize 430×225、MinimumSize 404×232、FixedToolWindow/SizableToolWindow、CenterParent；图片 48×48。

---

## 6. tips.txt（多语言提示文本，逐行一条）

加载规则（`analysis/tools/Tips.decompiled.cs` 273-287）：从 `Tips.dll` 所在目录起，按 `CurrentUICulture` 及其父 culture 找 `<culture>/tips.txt`，最后回退根目录 `tips.txt`；**每行 = 一条提示**；显示时把字面 `\n`/`\r\n`/`\t` 反转义（165-169）。因此文件里大量 `\n` 是**字面两个字符**。

发行包内文件：
- 根：`.../LogoBuilder_v1.6中文版/tips.txt`（**英文**，8 条）
- `es/tips.txt`（西班牙语，译者 amnesia）
- `it/tips.txt`（意大利语，译者 bovirus）
- `pt/tips.txt`（葡萄牙语，译者 Typhus_ @ XDA & MoDaCo）
- `ru/tips.txt`（俄语，无译者署名）
- `zh-Hans/tips.txt`（简体中文，译者"忘记月亮"/移动叔叔论坛）

### 6.1 英文原文（根 tips.txt，全文 8 条）

```
To create new project you just need to select a logo.bin file and specify a project directory.
You can specify program language explicitly adding it's short name to command line, eg. "LogoBuilder en-US".
You can place update-binary or updater-script files to project directory, so the program will use them in update.zip creation.
After creating a project program will search for update-binary and updater-script files in its directory and copy them to the project directory for using in update.zip creation.\nThis feature can be used for program adaptation for a specific phone model or CWM version.
You don't need convert BMP or JPG images to PNG, you can just rename them to imgN.png (this feature is provided by the Image class from .NET framework).
By default program creates update.zip file, appropriated for ZP100 and ZP300 based on mtk6575. (it flashes /dev/logo)
Secret feature!\nYou can make a boot_logo file via this program. Just try drag an image file to the program.
Any questions?\nFound a bug?\nWish to translate program to your language?\n\nEmail to ankadnikov@gmail.com
```

### 6.2 简体中文（`zh-Hans/tips.txt`，全文；`\n` 为字面转义）

```
\n制作个性化第一屏，只需要官方线刷包里的logo.bin文件！\n也可以在移动叔叔论坛查找同分辨率机型的logo.bin文件！
\n你可以指定程序的语言，将它的简称添加到命令行，例如 "LogoBuilder en-US"。
\n你可以替换update-binary或updater-script文件到工作目录, 本软件将会为你完成打包工作。
\n创建项目的时候，本软件会搜索当前目录的update-binary和updater-script脚本文件，然后拷贝到工作目录进行打包第一屏卡刷补丁包。\n\n这个开机第一屏卡刷布丁包适用于移动叔叔CWM Recovery！
\n你不需要转换BMP/JPG图片为PNG格式，你只需要重命名他们为 imgN.png (但本功能需安装.NET框架支持)。
\n默认情况下，程序创建的第一屏卡刷补丁包，适用于卓普ZP100和ZP300，其他机型待测。
\n开机第二屏也能做哦！\n\n只要选择好一个图片即可生成！
\n还有疑问?\n\n发现新BUG?\n\n作者邮箱 ankadnikov@gmail.com
\n简体中文：忘记月亮\n\n更多精彩内容，尽在移动叔叔论坛！
```

> 中文版 tips.txt **没有** "Secret feature / 拖拽生成 boot_logo" 这条的第 7 行对应句，而是第 8 行"开机第二屏也能做哦"。

### 6.3 其它语言（各 8 条，内容为英文 8 条之翻译；完整文件见对应目录）

| 语言 | 文件 | 首条（截断） | 译者/备注 |
|---|---|---|---|
| es | `es/tips.txt` | `Para crear nuevo proyecto sólo tiene que seleccionar un archivo logo.bin ...` | `Traducido al Español por amnesia.` |
| it | `it/tips.txt` | `Per creare un nuovo progetto devi solo selezionare un file logo.bin ...` | `Tradotto da bovirus (bovirus@gmail.com)` |
| pt | `pt/tips.txt` | `Para criar um novo projeto, apenas é necessário selecionar um ficheiro logo.bin ...` | `Traduzido por Typhus_ @ XDA & MoDaCo` |
| ru | `ru/tips.txt` | `Чтобы создать новый проект просто выберите файл logo.bin ...` | 无署名 |

**重建版建议**：
1. 提示文本改为结构化 `tips.json`（id/语言/富文本），保留中文版全部语义；
2. 字体/窗口做成可自适应（原版 430×225 Label 放不下中文长提示，见 N9）；
3. "秘密功能：拖图片到窗口生成 boot_logo" 在重建版中升级为显式按钮（已实现），提示文本保留说明；
4. 不要保留"移动叔叔/卓普ZP100"等 2012 年机型/论坛品牌文案，可改写为"适用 MTK 6575/67xx 老机型，其他机型请自测"。

---

## 7. update.zip 模板（卡刷包）

### 7.1 模板来源与结构

- 模板 = 主程序 manifest 资源 `LogoBuilder.update.zip`（解密后位于内嵌 `ResourceAssembly`，提取文件 `analysis/resources/ra_LogoBuilder.update.zip`，126,439 B，SHA-256 `cc15a4c5…`）。
- zip 条目（实测）：
  | 条目 | 原始大小 | 压缩后 | CRC32 |
  |---|---|---|---|
  | `META-INF/MANIFEST.MF` | 310 | 216 | e8b5f393 |
  | `META-INF/CERT.SF` | 363 | 246 | e784597f |
  | `META-INF/CERT.RSA` | 1,714 | 1,153 | f61d4c24 |
  | `META-INF/com/google/android/update-binary` | 190,484 | 123,878 | 61d41cc7 |
  | `META-INF/com/google/android/updater-script` | 184 | 128 | e93963a4 |
  | `logo.bin` | **模板中缺失**（MANIFEST.MF 却列有它的 SHA1，见下）| — | — |
- `update-binary` = ARM ELF 的 CWM 通用 update-binary（2012 年前后，190,484 B，SHA-256 `7a6e70ae1605188f97e624a47092a4954659b164958bbe66685de7500b21644a`）。
- `updater-script` 原文（184 B，SHA-256 `d7cb4523…`）：
  ```
  ui_print("Flashing logo...");
  show_progress(0.200000, 0);
  package_extract_file("logo.bin", "/dev/logo");
  ui_print("Patched!");
  ui_print("");
  ui_print("Now you can reboot your phone");
  ```
- `MANIFEST.MF`（310 B，SHA-256 `2ea10c34…`）：
  ```
  Manifest-Version: 1.0
  Created-By: 1.0 (Android SignApk)

  Name: logo.bin
  SHA1-Digest: PoawPHjKHs5PuMYi0ylP3Fh9SF0=

  Name: META-INF/com/google/android/updater-script
  SHA1-Digest: VtwxcA2Ds1s/fX/cn4hoCEQ8TNs=

  Name: META-INF/com/google/android/update-binary
  SHA1-Digest: evpQZxxI+Xyqg8nf29FXd/YvBbc=
  ```
- `CERT.SF`（363 B，SHA-256 `884d16e2…`）包含同样的三个条目 + `SHA1-Digest-Manifest`；`CERT.RSA`（1,714 B，SHA-256 `650f15f2…`）= SignApk 生成的 PKCS#7 签名。

### 7.2 原版生成行为（`LogoProject.MakeZip` 3306–3367）

1. 以模板 zip 为底（`new ZipFile(GetManifestResourceStream("LogoBuilder.update.zip"))`）；
2. `zip.AddFile(logoPath, "")` → 根目录 `logo.bin`（路径由 `logoPath` 的**文件名**决定，原版固定 `<项目>/logo.bin`）；
3. 若项目目录有 `updater-script` → 以目录 `META-INF\com\google\android`（**反斜杠**）添加；`update-binary` 同理；
4. `SaveZip`：目标已存在先 `File.Delete`，再 `zip.Save`。

### 7.3 对重建版的结论

- ✅ **可直接复用模板**（`analysis/resources/template_*` 与 `ra_LogoBuilder.update.zip` 均可用）；`docs/spec_api.md` 的 `make_update_zip(...)` 契约正确。
- ⚠ 原版**在加入新 logo.bin 后没有重新签名**（CERT.SF/MANIFEST.MF 仍是模板旧摘要，且 MANIFEST 里 logo.bin 摘要对应的是模板原有 logo.bin）。CWM 通常不校验签名所以能用；重建版若提供 `sign_stub` 选项应说明"不保证签名有效"。
- ⚠ 自定义脚本目录**必须用 `/` 分隔**（原版反斜杠问题 N7），重建版应写 `META-INF/com/google/android/updater-script`。
- ⚠ 现代机型（A/B 分区、fastboot flash logo）应额外支持**单文件 logo.img**（spec §6 目标 5）；本模板仅适用于 CWM 卡刷旧机型。

---

## 8. 建议复用原文案（逐字）清单

**沿用原版中文（用户熟悉）：**
`错误`、`取消`、`确定`、`图像大小`、`图像尺寸和设置值不对应{0}。\r\n你必须修复该错误或删除的图像文件。`、`没有发现图片文件。\r\n需要注意的是图像的数字，必须从1开始。`、`打包新logo.bin错误\r\n{0}`、`创建update.zip失败\r\n{0}`、`无法读取.project文件`、`不能创建第二屏文件{0}`、`选择已有项目的工作目录`、`选择新建项目的工作目录`、`无法新建项目\r\n{0}`、`启动时显示提示`、`上一个提示`、`下一个提示`、`关闭`、`显示提示`、`打开项目主页`、`打开项目目录`、`新建项目`、`打开项目`、`选择图片创建开机第二屏`、`从开机第二屏提取图片文件`、`图片文件|*.bmp;*.jpg;*.png|所有文件 (*.*)|*.*`、`16 bit (MTK65xx)` / `32 bit (MTK67xx)`。

**保留英文（品牌/技术标识）：**
`Logo Builder`、`logo.bin`、`logo.img`、`boot_logo`、`update.zip`、`MTK65xx`、`MTK67xx`、`CWM`、`/dev/logo`。

**需要改写（原版有误/有品牌）：**
1. `cantOpenProject` 中文"无法新建项目" → **"无法打开项目"**（原版复制粘贴错误）；
2. `btnOpen.Text` 中文"打开工作目录" → **"打开项目"**；
3. `btnCreate.Text` 中文"制作新的logo.bin" → **"新建项目"**；
4. `takeIt`/`btnPack.ToolTip` 的"移动叔叔/忘记月亮" → 中性文案；
5. `dataLengthError1/2` → 具体化错误信息（含数值与原因）；
6. `imageSizeError` → 在打包前真正使用（原版是死字符串）；
7. 语言选择从"只能命令行参数"改为"设置项 + CLI `--lang`"。

**原版没有、重建版建议新增的文案类别：**
文件格式错误（magic/偏移/长度自洽）、自动推断分辨率候选说明（"内置尺寸表精确匹配/相邻块一致/宽高比匹配"）、打包前校验与一键修复（尺寸不符/缺图/多余块）、单文件 logo.img 导出、分区大小约束、ignored 尺寸管理、关于/许可证（注明格式来源为 MTK LOGO 分区）。
