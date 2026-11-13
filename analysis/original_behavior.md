# LogoBuilder v1.6 原版行为规格（逆向证据文档）

> 目标：`LogoBuilder_jb51/LogoBuilder_v1.6中文版/LogoBuilder.exe`
> SHA-256：`f6a9a037c89c0a80e73f99cc7617773c993b0ffc71609fcab75503111c512fed`（417,792 B，.NET Framework 4.0 Client Profile，WinForms，AnyCPU）
> 保护：Crypto Obfuscator for .NET（控制流混淆、字符串加密、方法调用代理、反调试）
> 证据来源（全部静态、未运行原程序）：
> - `analysis/decompiled/LogoBuilder.cs`：ILSpy 4.0 全量反编译（9097 行，含混淆控制流；行号即本文引用行号）
> - `analysis/il/il/*.il`、`analysis/il/methods.jsonl`：逐方法 IL/方法表
> - `analysis/tools/il_A/il/*.il`：`A.*` 混淆辅助类型 IL（反混淆基础设施）
> - `analysis/tools/stringpool_dump.txt`、`analysis/decrypted_strings_clean.txt`：反射解出的 Crypto Obfuscator 字符串池（137 条全部经真实解密器往返校验）
> - `analysis/tools/call_resolution.txt`：把混淆调用代理还原为真实目标（`LogoBuilder.*` / BCL）后的调用清单
> - `analysis/resources/strings/*.txt`：从 .resources 提取的 UI 字符串（中性英文 + zh-Hans）
> - `analysis/resources/ra_*.resources`：从内嵌 `ResourceAssembly` 解出的 WinForms 资源
> - `analysis/tools/Tips.decompiled.cs`：Tips.dll 完整反编译
> - `analysis/tools/string_usages.tsv`：字符串偏移 → 反编译行号/方法 的使用索引
>
> 标注约定：**【证据】**直接来自代码/IL/资源；**【推断】**由上下文推断、未逐指令验证；**【未决】**无法静态确认。
> 反编译代码中 `\u000b.\u0002(NNN)` 之类即字符串解密器调用（`NNN` 是字符串池字节偏移），本文已尽量替换为真实字符串（见 `analysis/decrypted_strings_clean.txt`）。

---

## 0. 反混淆与关键结论（先读）

1. **字符串池已完全恢复**：`A.\x0b` 类静态字段 `\x01`（解密后 193,536 B）= `[0,5195)` 长度的 UTF-16 字符串表 + `[5195,193536)` 打包的 WinForms 资源块（MainForm/SizeDialog/SignControl/Properties .resources 与 update.zip 的明文副本，已逐一比对命中）。字符串池记录格式 = `[变长字节长度(0x80/0x40 位)] + UTF-16LE`；137 条记录全部用真实解密器 `A.<class>::<method>(int offset)` 回放校验一致。
   **因此反编译中所有被加密的 UI 文案、文件名、错误串、CLI 命令、URL 均已还原。**
2. **混淆调用代理已破解**：启动时 `A.<init>::<method>(typeToken, methodToken, declType)` 用元数据 token 把真实方法绑定到代理委托；`analysis/tools/call_resolution.txt` 给出每个 `contact` 的真实目标。据此确认了 `Program.Main`、`MainForm` 每个按钮、`SizeDialog`、`LogoProject` 的真实语义。**【证据】**
3. **反调试**：`Program.Main` 第一句 `A.\t::\x13()`（IL token 0x06000063）在检测到调试器时直接 `throw new Exception("A debugger was detected! This software cannot run while a debugger is active.")`（字符串池偏移 4882；`OLLYDBG` 检测串偏移 4867；P/Invoke `IsDebuggerPresent`/`CheckRemoteDebuggerPresent`/`EnumWindows` 偏移 5063/5098/5172）。**任何"运行原版 exe"的动态取证都会被它拒绝或干扰**——这也是本仓库坚持静态分析的原因。**【证据】**
4. 8 个内嵌资源已全部有归属：manifest `LogoBuilder`(235,832 B) 经同一解密器解出 **ResourceAssembly PE**（193,536 B，AssemblyTitle=`ResourceAssembly`，Company=LogicNP Software），其 manifest 资源为 `LogoBuilderLogoBuilder`(1,977 B) + `LogoBuilder.update.zip`(126,439 B) + 5 个 WinForms `.resources`。`LogoBuilderLogoBuilder` 是字符串池的二次 DES+Deflate 包装（详见 ui_assets/manifest.md）。**【证据】**

---

## 1. 程序入口 `Program`（`LogoBuilder.cs` 6536–6695）

### 1.1 `[STAThread] Program.Main(string[] args)`（6541–6578）

按执行顺序：
1. `A.\t::\x13()` —— **反调试守卫**（见 §0.3）：检测到调试器即抛异常，进程直接崩溃并弹"Error"。**【证据】**（IL token 0x06000063/0x06000064）
2. `if (args.Length >= 3)` → `RunArgs(args[0], args[1], args[2])`；然后 `Environment.Exit(errCode)`；**return，不创建任何窗体**（6544–6561）。多余参数被忽略；`args.Length > 3` 同样只取前三个。**【证据】**
3. `if (args.Length == 1)`（6562–6571）：
   ```csharp
   try { Thread.CurrentThread.CurrentUICulture = new CultureInfo(args[0]); } catch { }
   ```
   即**唯一参数是语言（culture）名**，如 `LogoBuilder en-US`、`zh-Hans`、`ru-RU`（与 tips.txt 描述一致）。culture 名非法则**静默忽略**（catch 空），界面回落到系统 UI culture。**【证据 + 推断组合】**：代理链已确认 `Thread::get_CurrentThread`、`CultureInfo::.ctor`、`Thread::set_CurrentUICulture`。
4. `Application.EnableVisualStyles();`（6573；代理→`Application::EnableVisualStyles`）**【证据】**
5. `Application.SetCompatibleTextRenderingDefault(false);`（6574）**【证据】**
6. `Application.ThreadException += ApplicationOnThreadException;`（6575）**【证据】**
7. `AppDomain.CurrentDomain.UnhandledException += CurrentDomainOnUnhandledException;`（6576）**【证据】**
8. `Application.Run(new MainForm());`（6577）**【证据】**（`MainForm::.ctor` + `Application::Run` 代理已解析）

> 未设置 `Application.SetUnhandledExceptionMode`；`SetHighDpiMode`/DPI 相关调用**不存在**（.NET 4.0 时代），程序为系统 DPI 感知的旧式 WinForms。**【证据：无相关调用】**

### 1.2 异常处理器（6580–6588）

两个处理器都是同一行为：`MessageBox.Show(ex.ToString(), "Error", OK, Error图标)`。
- 字符串池 257 = `"Error"`；257 同时是各 `rLoc.GetString("error") ?? "Error"` 的回退值（StringManager 无该键时）。**【证据】**

### 1.3 命令行模式 `RunArgs(command,arg1,arg2)`（6590–6695）

初始化（6594）：`LogoProject.Init(new ResourceManager("LogoBuilder.Strings", typeof(MainForm).Assembly), null)`。
**关键：CLI 模式 `dialogOwner = null`** —— 之后所有需要交互的尺寸选择都会被跳过（见 §4.3、B1）。**【证据】**

| 命令（字符串池偏移） | 语义 | 实际调用（已解析） | 失败/退出码 |
|---|---|---|---|
| `unpack`(3135) | 解包 bin → 项目目录 | `LogoProject.CreateProject(arg1, arg2, null)`（arg1=logo.bin，arg2=项目目录） | 异常→ `errCode = LogoProject.ErrorCode`；若仍为 0 则 `errCode=2` |
| `pack`(3148) | 项目目录 → 打包 bin | `LogoProject.OpenProject(arg1)`；`LogoProject.PackProject(null, arg2)` | 同上 |
| `bl2img`(3157) | boot_logo → PNG | `LogoProject.BootLogoToImage(arg1)`；`image.Save(arg2, ImageFormat.Png)` | 同上 |
| `img2bl`(3170) | 图片 → boot_logo（裸 RGB565） | `LogoProject.ImageFromFile(arg1)`；`LogoProject.ImageToBootLogo(img, arg2, null)` | 同上 |
| 其他/空 | 未识别 | — | `errCode = 1` |

- 成功时 `errCode==0` → `Environment.Exit(0)`；命令未识别=1；异常=LogoProject.ErrorCode（2/3/4/6/7，见 §6.7）否则 2。**【证据】**
- `RunArgs` 中 `OpenProject` 返回值被 `pop` 丢弃：**项目不存在/缺 header.bin 时不会立刻报错**，随后 `PackProject` 在 `header==null` 时于 3200 行 `header,0,4` 抛 `NullReferenceException`/`ArgumentException`，最终退出码 2（信息不友好）。**【证据：3198-3202 无 header 判空】**
- 命令比较为 `string.op_Equality`（大小写敏感）。**【证据】**

---

## 2. 设置与持久化（`LogoBuilder.Properties.Settings` 4106–4194）

`Settings : ApplicationSettingsBase`，5 个 `[UserScopedSetting]`：

| 属性 | 类型 | 默认值 | 用途（调用点） |
|---|---|---|---|
| LastProjectPath | string | "" | 新建项目成功后保存（AfterProjectCreated 5442）|
| LastLogoIndex | int | 0 | `ofdLogoBin.FilterIndex`（LoadSettings 5260；新建成功后 5333-5334 回写）|
| LastOpenedLogo | string | "logo.bin" | `ofdLogoBin.FileName`（LoadSettings 5261；成功后回写）|
| LastSavedBootLogo | string | "" | boot_logo 保存对话框的默认文件名/目录（MakeBootLogo 5901-5923）|
| LastColorDepthIndex | int | 0 | SizeDialog 颜色位深下拉的恢复（InitColorDepths 4322-4337）|

**落盘位置（与 docs/spec_original.md 5.1 第 8 条不一致，见 §10）**：
- 上述 `ApplicationSettingsBase` 用户设置：.NET Framework 标准机制写 `<LocalApplicationData>\<Company>\<Product>\<exe>_Url_<hash>\<version>\user.config`；本程序 Company=""、Product="LogoBuilder"，实际为 `%LOCALAPPDATA%\LogoBuilder\...\user.config`（**不是** `%APPDATA%`，也不是程序目录）。`LogoBuilder.exe.config` 中 `userSettings` 段只是设计期默认值。**【证据：4106-4194 + exe.config；user.config 具体路径为 .NET 框架规则推断】**
- **`sizes.txt` / `ignored.txt` 写在 exe 所在目录**，不是用户目录：`LogoProject.SaveSettings`（2123–2246）用 `Application.ExecutablePath`→`Path.GetDirectoryName`→拼接 `sizes.txt`(397)/`ignored.txt`(416) 后 `StreamWriter` 覆盖写。**【证据】**
- 每次 `Settings.Default.Save()` 立即落盘（如 SizeDialog 改位深 4552–4556、Tips 每个 tip 切换）。

### 2.1 `LogoProject.SaveSettings()`（2123–2246）细节

- 写 `sizesDict` 的**全部值**（内置+用户增量合并后的字典），每行 `LogoSize.ToString()` = `"{0}x{1}x{2}"`（1039）逐行 `WriteLine`；字典枚举顺序 = 插入序（不确定，写回文件顺序不稳定）。**【证据】**
- 写 `ignoredSizes` 每个 int 一行（`WriteLine(int)`）。**不去重**：用户取消对话框路径（3010）和异常路径（3016）直接 `List.Add`；`LoadSizes` 从文件读入时同样直接 Add（2411），因此多次"取消"会产生重复行、文件逐渐膨胀。**【证据：2406-2412, 3007-3017】**

### 2.2 `LogoProject.LoadSizes()`（2248–2431）

- `File.ReadAllLines(exeDir/sizes.txt)`；每行 `Split("x")`（字符串 439 = `"x"`），`StringSplitOptions.RemoveEmptyEntries`；**至少 2 段**才处理；`int.TryParse` 解析 W/H（**当前 culture**），失败则跳过；第 3 段（若有）TryParse 为 colorDepth，**失败或缺失默认 2**。**【证据】**
- 只校验是否重复 key（`sizesDict.ContainsKey`），**不校验 width/height>0、不校验 colorDepth∈{2,4}**；`LogoSize.ColorDepth` setter 会把非法值静默改成 2（4036-4055），但 width/height=0 或负数会原样入字典 → `Key=0/-N`。**【证据】**
- `ignored.txt` 同样逐行 `int.TryParse` 后 Add。**【证据】**
- 整个 `LoadSizes` 外层 try/catch：任何异常 → `MessageBox.Show(ex.Message, rLoc.GetString("error") ?? "Error")`（2426–2430）。因此 **sizes.txt 权限/编码问题会以"错误"弹窗出现**，但程序继续（字典可能半空）。**【证据】**
- `LoadSizes` 何时调用：`LogoProject.Init`（2110–2115）里第一句 `\u001a\u0004.\u0002()` = `LoadSizes()`。即**每次主窗体构造时重新读配置**。**【证据】**

---

## 3. 主窗体 `MainForm`（4951–6217）

### 3.1 布局（`InitializeComponent` 6089–6216 + `ra_LogoBuilder.MainForm.resources`）

| 控件 | 关键属性（资源实测）| 用途 |
|---|---|---|
| Form | ClientSize 787×445；MinimumSize 655×320；StartPosition=CenterScreen；AutoScaleMode=Font (6,13)；Icon=32×32；ShowIcon(默认)；SizeGripStyle=Show；Text="Logo Builder"；AllowDrop=true | 主窗口 |
| panel1 | Dock=Top；Size=787×42；TabIndex=6 | 顶部按钮条 |
| flowLayoutPanel1 | Location(12,46)；Size 763×320；Anchor=四边；AutoScroll=true；TabIndex=7 | 图像缩略图流式布局区 |
| progressBar1 | (37,410)；713×23；Anchor=Bottom,Left,Right；Visible=false；TabIndex=2 | 打包/生成进度 |
| btnPack | (294,372)；199×32；Anchor=Bottom；Text="Make"；Visible=false（初始隐藏）；TextImageRelation=ImageBeforeText | 打包按钮（有项目后显示）|
| btnOpenFolder | (12,372)；32×32；Anchor=Bottom,Left；Image=16×16；Visible=false；ToolTip | 资源管理器打开项目目录 |
| btnCreate | panel1 (3,3)；148×36；Image=24×24；Text="Create new project" | 新建项目 |
| btnOpen | panel1 (157,3)；148×36；Image=24×24；Text="Open existing project" | 打开项目 |
| btnOpenBootLogo | panel1 (311,3)；80×36；Image=72×24；ToolTip | boot_logo → 图片 |
| btnCreateBootLogo | panel1 (397,3)；80×36；Image=72×24；ToolTip | 图片 → boot_logo |
| btnWeb | panel1 (634,3)；32×36；Image=24×24 | 打开主页 |
| btnTips | panel1 (672,3)；32×36；Image=24×24；ToolTip="Show tips"；Visible 由 InitTips 决定 | 提示 |
| sign | panel1 (710,3)；74×37 | 作者/捐赠控件（SignControl）|
| ofdLogoBin | Filter="logo.bin|logo.bin|logo.img|logo.img|All files (*.*)|*.*"；RestoreDirectory=true | 选 logo.bin |
| ofdImage | Filter="Image files|*.bmp;*.jpg;*.png|All files (*.*)|*.*" | 选图片 |
| ofdBootLogo | Filter="boot_logo|boot_logo|All files (*.*)|*.*" | 选 boot_logo |
| saveFileDialog1 | — | 保存 boot_logo / boot_logo.png |
| toolTip1 | 所有按钮 hover 提示（文案见 docs/original_ui_strings.md）| |

- 顶部按钮顺序（ZOrder/位置）：btnCreate, btnOpen, btnOpenBootLogo, btnCreateBootLogo, btnWeb, btnTips, sign。
- 事件绑定：btnPack.Click / btnOpenFolder.Click / btnCreate.Click / btnOpen.Click / btnTips.Click / btnCreateBootLogo.Click / btnWeb.Click / btnOpenBootLogo.Click；Form.FormClosing=MainForm_FormClosing；Form.Shown=FormShown；Form.DragDrop=MainForm_DragDrop；Form.DragEnter=MainForm_DragEnter。**【证据 6089-6216 + 资源 manifest】**
- **无菜单栏（无 MainMenu），无 KeyDown/快捷键处理**；键盘交互仅 Tab 顺序与对话框默认按钮（SizeDialog 的 AcceptButton=btnOk、CancelButton=btnCancel）。**【证据：InitializeComponent 无键盘事件绑定；4665-4744 中 4722/4727 设置 Accept/CancelButton】**

### 3.2 构造函数（4995–5003）

顺序（IL 已解析）：
1. `rLoc = new ResourceManager("LogoBuilder.Strings", typeof(MainForm).Assembly)`（4955 field init）
2. `InitializeComponent()`
3. `LoadSettings()`（= `ofdLogoBin.FilterIndex = Settings.Default.LastLogoIndex; ofdLogoBin.FileName = Settings.Default.LastOpenedLogo;`）
4. `InitTips()`（= 若 exe 目录存在 `Tips.dll`（字符串 1549）则 `btnTips.Visible=true`，否则隐藏）
5. `SetVersion()`（= `this.Text = string.Format("Logo Builder v. {0}", AssemblyFileVersionAttribute.Version)`，格式串偏移 1510）
6. `LogoProject.Init(rLoc, this)` —— **dialogOwner=this，GUI 路径 owner 恒非空**

> 注意：MainForm 构造期间 **不** 显示 tips；tips 在 `FormShown` 时弹出（见 3.7）。

### 3.3 新建项目 `btnCreate_Click`（5264–5341）

1. 先构造（不显示）`FolderBrowserDialogEx`：`CreateFolderBrowserDialog(rLoc.GetString("selectNewProjectFolder") ?? "Select new project folder", showCreateButton:true)`（5266–5267；描述串偏移 1593，回退 1724 语句在别处）。
2. `ofdLogoBin.ShowDialog(this)`（5268）；**取消则直接 return**（不弹任何提示）。
3. `folderBrowserDialog.ShowDialog(this)`（5282）；取消则 return。
4. `ClearProject(); SetProgressState(true);`
5. `try { success = LogoProject.CreateProject(ofdLogoBin.FileName, folderBrowserDialog.SelectedPath, PackingProjectProgressHadler); }`
   `catch { MessageBox.Show(string.Format(rLoc.GetString("cantCreateProject") ?? "Can't create project\r\n{0}", ex.Message), "Error", Error); }`
   `finally { SetProgressState(false); }`（5284–5330）
6. `if (success)`：回写 `Settings.Default.LastLogoIndex = ofdLogoBin.FilterIndex`、`LastOpenedLogo = ofdLogoBin.FileName`、保存；`AfterProjectCreated()`（5331–5337）。
- `PackingProjectProgressHadler(value,max)`（5359–5363）= `progressBar1.Maximum=max; progressBar1.Value=value;`。
- **无输入校验**：项目目录只来自文件夹对话框，不检查可写/非空/是否已存在项目；对已存在目录会直接覆盖 `header.bin/.project/imgN.png`（`CreateProject` 中无检查）。**【证据 2677-2733】**

### 3.4 打开项目 `btnOpen_Click`（5375–5438）

1. `CreateFolderBrowserDialog(rLoc.GetString("selectExistingProjectFolder") ?? "Select existing project folder", showCreateButton:false)`（5377–5378；偏移 1724/1779/1810）。
2. 对话框 OK → `ClearProject()` → `if (LogoProject.OpenProject(selectedPath)) AfterProjectCreated();`
   - `OpenProject` 返回 false 时**完全静默**（无提示）。
3. `catch` → `MessageBox.Show(string.Format(cantOpenProject ?? "Can't open project\r\n{0}", ex.Message),"Error")`（5419–5436）。

### 3.5 打包 `btnPack_Click`（5614–5721）

1. `SetProgressState(true);`
2. `if (!RefreshImages()) return;`（有任一张图加载/尺寸校验失败则禁用打包，函数内已弹过错误）
3. `try { LogoProject.PackProject(PackingProjectProgressHadler, ProjectPath + "logo.bin"); } catch { MessageBox(errorPacking + ex.Message); return; }`（5633；偏移 1855=`logo.bin`、1872=`errorPacking`、1897 回退串）
4. `try { LogoProject.MakeUpdateZip(ProjectPath + "logo.bin", ProjectPath + "update.zip"); } catch { MessageBox(errorCreatingUpdate + ex.Message); }`（5674；偏移 1960=`update.zip`、1981=`errorCreatingUpdate`）
5. `finally { SetProgressState(false); }`
- **固定输出名**：`<项目目录>\logo.bin`、`<项目目录>\update.zip`，**无另存为对话框**（与 spec 5.1 "保存对话框" 不符，见 §10）。**【证据 5633/5674】**

### 3.6 图像区与文件监视

`FillPictureBoxesList()`（5077–5167）：
- 遍历 `LogoProject.BlockList`；只处理 `StartsWith("image:")`（652）的行；`Split(' ', RemoveEmptyEntries)`；**段数 <3 跳过**；
- `int.Parse(array[1])`、`int.Parse(array[2])`（**当前 culture**，无 try/catch → 非法 .project 会抛 FormatException 并冒泡到 ThreadException"Error"）；`array[3]` 存在则 `int.TryParse` 为 colorDepth（否则 2）；
- 构造 `ImagePreviewControl(new LogoSize(w,h,depth), ProjectPath+imageName)`；`Name = string.Format("pictureBox{0}", i+1)`（偏移 1566）；`Size=76×135`；`Location=(0,i)`；`Tag=i`；`DefaultImageSizeChanged += PictureBoxOnDefaultImageSizeChanged`；加入 `flowLayoutPanel1.Controls`。

`PictureBoxOnDefaultImageSizeChanged`（5169–5203）：`blockIndex=(int)Tag`；若 < BlockList.Count → `LogoProject.ChangeImageSize(index, e.NewSize)`（重编码该 PNG 为新尺寸并回写 .project/sizes）；`control.DefaultImageSize=newSize`；`control.RefreshImage()`；异常 → `MessageBox.Show(ex.Message)`（无标题、无前缀）。

`RefreshImages()`（5558–5612）：对每个预览控件 `RefreshImage()`；ToolTip 显示控件的 `ErrorMessage`（正常为空）；`btnPack.Enabled = 全部正常 && 控件数>0`。`RefreshImage` 返回 false 即图片缺失/尺寸不符（见 §5）。

`CreateFileSystemWatcher()`（5467–5479）：`new FileSystemWatcher(ProjectPath)`、`IncludeSubdirectories=true`、`NotifyFilter=FileName|Size|LastWrite|CreationTime`；Changed/Created/Deleted/Renamed 全部 → `OnFileSystemChanged`。
`OnFileSystemChanged`（5526–5556）：`Thread.Sleep(500)`；`BeginInvoke(RefreshImages)`；**异常全吞**（try/catch 空）。`RemoveFileSystemWatcher`（5481–5524）注销事件并 Dispose。
- **已知竞态**：watcher 回调里 `BeginInvoke` 前主窗体可能已关闭 → `InvalidOperationException` 被空 catch 吞掉；多次快速外部改图会连续 500ms 阻塞 watcher 线程（非 UI 线程，无碍 UI）。

`ClearProject()`（5365–5373）：`LogoProject.Clear()`；隐藏 btnOpenFolder/btnPack；`progressBar1.Visible=false`；`RemoveFileSystemWatcher()`；`ClearPictureBoxes()`。

`AfterProjectCreated()`（5440–5465）：`Settings.Default.LastProjectPath=ProjectPath; Save();`；若 `ProjectPath != null`：`btnOpenFolder.Visible=true`；`CreateFileSystemWatcher()`；`FillPictureBoxesList()`；`RefreshImages()`。

### 3.7 其它按钮 / 交互

| 成员 | 行为（行号） |
|---|---|
| `btnOpenFolder_Click` | `Process.Start(LogoProject.ProjectPath)`（5723–5726）——用资源管理器打开项目目录。字符串池无相关 URL。 |
| `FormShown` | 调用 Tips 的启动入口（`Tips.TipsMain.StartUp(this)`；本程序通过反射加载 Tips.dll，见 §7）（5728–5731）|
| `pbTips_Click` | `Tips.TipsMain.Show(this)`（5733–5736）|
| `btnWeb_Click` | 若 `CultureInfo.CurrentUICulture.Name` **不以 "ru" 开头**（`StartsWith`，偏移 2081）→ 打开 `http://sites.google.com/site/kadanutilities/home/logobuilder-en`（2086），否则 `http://sites.google.com/site/kadanutilities/home/logobuilder`（2213）——按**语言**而非国家分流（5738–5762）|
| `MainForm_FormClosing` | `LogoProject.SaveSettings()`；异常 → `MessageBox.Show(ex.Message,"",Error)`（无标题）（5764–5774）|
| `OnMouseWheel` | 若 flowLayoutPanel 竖向滚动条可见 → `VerticalScroll.Value = Clamp(Value - e.Delta*70, Minimum, Maximum)` 后刷新；**固定 70px/格**，系统每格 delta=120 → 每格滚动 8400px?! 实际 e.Delta 单位为 120 的倍数，代码乘 70 得到 8400，被 Clamp 到 Maximum → **一次滚轮直接滚到底/顶**（5776–5787）。**这是可复现的可用性缺陷 N4**。|
| `MainForm_DragEnter` | 仅接受 `FileDrop` 且**恰好 1 个**文件且 `File.Exists` → `Effect=All`，否则 None（5789–5840）|
| `MainForm_DragDrop` | 同样条件 → `MakeBootLogo(file)`（5842–5893）|
| `MakeBootLogo(file)` | 选保存位置：若 `Settings.Default.LastSavedBootLogo` 非空→`saveFileDialog1.InitialDirectory=Path.GetDirectoryName(该值)`、FileName=该值；否则 InitialDirectory=ProjectPath、FileName="boot_logo"（2334）。OK → ProgressBar=100、SetProgressState(true)、`LogoProject.ImageToBootLogo(image, path, progress=>{progressBar1.Value=percent; Application.DoEvents();})`；成功后 `Settings.Default.LastSavedBootLogo=path; Save()`；异常 → `errorCreatingBootLogo + ex.Message`（5895–6005）|
| `btnCreateBootLogo_Click` | `ofdImage.ShowDialog` → OK → `MakeBootLogo(fileName)`（6007–6026）|
| `btnOpenBootLogo_Click` | `ofdBootLogo.ShowDialog` → OK → `image = LogoProject.BootLogoToImage(file)`；saveFileDialog 默认名 `boot_logo.png`（2459）；OK → `image.Save(path, ImageFormat.Png)`；异常 → `MessageBox.Show(ex.Message, error ?? "Error")`（6028–6077）|
| `SetProgressState(bool)` | 遍历 `panel1.Controls` 设 `Enabled = !progress`（禁用所有顶部按钮）；`progressBar1.Visible=progress`；结束时若窗体未聚焦则 `Focus()`（5005–5056）|
| `SetVersion` | 见 3.2-5 |

### 3.8 右键菜单（ShellContextMenu）

`ImagePreviewControl.pictureBox1_MouseClick`（1566–1616）在**右键点击**且图片文件存在时：
`new ShellContextMenu().ShowContextMenu(Handle, new FileInfo[]{imagePath}, e.Location)`（偏移对应源码 1577–1588）。
`LogoBuilder.ShellContextMenu`（6697–8024）是完整的 Win32 Shell IContextMenu 封装：`SHParseDisplayName`/`SHBindToParent`/`GetUIObjectOf` 取 `IContextMenu2/3`，`LocalWindowsHook`（8062–8178，WH_CALLWNDPROC）转发 `WM_DRAWITEM/WM_MEASUREITEM/WM_INITMENUPOPUP/WM_MENUCHAR` 消息，最后 `TrackPopupMenu`；支持 Shift/Control 组合（7524/7546）。`TCHAR` 等见 6699-7234。**【证据】** —— 重建版不需要复刻（可用系统菜单/打开所在目录替代）。

---

## 4. 分辨率选择对话框 `SizeDialog`（4198–4745）—— B1 的完整失败路径

### 4.1 常量与状态

- `public const int MaxSize = 6000;`（4200）——宽/高上限（严格 `<6000`）。
- 字段：`byte[] buf`（原始块解密后字节）、`List<LogoSize> sizes`（候选列表）、`tbSize`(TrackBar)、`pictureBox1`(预览)、`lblSize`(尺寸文字)、`cbColorDepths`(位深)。
- **单例**：`LogoProject.dlgImageSize = new SizeDialog()`（2102，LogoProject 静态构造时创建一次，永久复用；从不 Dispose）。**【证据】**

### 4.2 初始化与选尺寸流程

1. **构造**（4316–4320）：`InitializeComponent(); InitColorDepths();`
   - `InitColorDepths`（4322–4337）：`cbColorDepths.Items` 加 `ColorDepthItem("16 bit (MTK65xx)", 2)` 与 `ColorDepthItem("32 bit (MTK67xx)", 4)`（偏移 1223/1256）；`cbColorDepths.SelectedIndex = Settings.Default.LastColorDepthIndex`（**无范围检查**，越界即抛 ArgumentOutOfRangeException —— 见新缺陷 N1）。
2. **`SetBuffer(byte[] buffer, LogoSize selectedSize)`**（4339–4404）：
   - 暂存 `buf`；解绑 `cbColorDepths.SelectedIndexChanged`；
   - **若 `selectedSize` 非空**：`ColorDepth = selectedSize.ColorDepth`（下拉框选中对应项）；
   - **否则若 `buffer.Length/2` 为奇数**：`ColorDepth=2` 且 **禁用**下拉框（`cbColorDepths.Enabled=false`）——因为 32bpp 数据总像素数必为偶数，奇数必然是 16bpp；
   - **否则**：`ColorDepth = LooksLike32Bit(buffer) ? 4 : 2`；启用下拉框；
   - `InitSizes()`；
   - **若 `selectedSize` 为空**：`FindNearestSize(LogoProject.FindNearestRatioForBufSize(buf.Length / ColorDepth, 0.5625))`
   - **否则**：`SelectedSize = selectedSize`（在候选列表中找等值项并定位 trackbar；找不到则**无动作**，trackbar 停在 InitSizes 复位后的 0 位置 → 实际返回 sizes[0]）；
   - 重新绑定事件。
3. **`InitSizes()`**（4406–4472）：
   - `sizes = new List<LogoSize>(100)`；`num = ColorDepth`；`num2 = buf.Length / num`（= 像素数）；
   - `for (i = 1; i <= num2; i++)`：若 `num2 % i != 0` continue（枚举因数对）；`num3 = num2 / i`；
   - `if (i >= 6000) continue; if (num3 < 6000) sizes.Add(new LogoSize(i, num3, num));`
   - `tbSize.Minimum=0; tbSize.Maximum = sizes.Count - 1;`
   - **失败路径核心**：像素数是**质数**或所有因数对都含 `≥6000` 的因子时，`sizes` 为**空** → `tbSize.Maximum = -1` 抛 `ArgumentOutOfRangeException`（TrackBar.Maximum 不能 < Minimum）。也可发生在 `buf.Length==0`（zlib 解出 0 字节）时。**【证据】**
4. **`FindNearestSize(double nearestSize)`**（4474–4532）：`nearestSize<=0` 直接 return；在 `sizes` 中找 `|W/H - nearestSize|` 最小的项；然后 `SelectedSize = sizes[num]`。若 `sizes` 空：`num=0` 不变 → `sizes[0]` → **IndexOutOfRangeException**（发生在 InitSizes 之后，所以正常走不到；但 `FindNearestSize` 也被 `cbColorDepths_SelectedIndexChanged` 调用，见下）。**【证据】**
5. **`tbSize_ValueChanged`**（4534–4545）：`lblSize.Text = string.Format("{0} x {1} ({2} bit)", W, H, ColorDepth*8)`（偏移 1289）；`pictureBox1.Image = LogoProject.StreamToImage(new BinaryReader(new MemoryStream(buf)), SelectedSize)` —— **每次拖动 trackbar 都完整解码一张图**；对 2MP 图逐像素循环（2449-2497）在 UI 线程，拖动会明显卡顿/白屏。**【证据】**
6. **`cbColorDepths_SelectedIndexChanged`**（4547–4567）：
   - `try { Settings.Default.LastColorDepthIndex = cbColorDepths.SelectedIndex; Settings.Default.Save(); } catch {}`（4552-4556）
   - 先取**当前** `SelectedSize` 的 W/H 比值 → `InitSizes()` → `FindNearestSize(ratio)`（切换位深时保持宽高比）。
   - **风险**：取 SelectedSize 时若 `sizes` 为空/索引失效（或 W=0）会抛异常（除法/索引），该段无 try/catch → 冒泡到 `Application.ThreadException` 弹 "Error"。**【证据】**
7. **`OnMouseWheel`**（4569–4629）：`base.OnMouseWheel` 后 `Delta>0 && Value<Maximum` → `tbSize.Value++`；`Delta<0 && Value>0` → `--`。**对话框内滚轮 = 微调滑块**（每次 ±1 候选）。**【证据】**
8. **`SelectedSize` getter**（4286–4291）= `sizes[tbSize.Value]` —— 若 `sizes` 空则必然越界。

### 4.3 对话框"不出现"的所有路径（B1 精确化）

| # | 触发条件 | 代码证据 | 用户可见现象 |
|---|---|---|---|
| B1-a | `LogoProject.Init(..., owner)` 传入 `owner == null`（只有 CLI `RunArgs` 会这样） | 2110-2115；DecompressBlock 2989 判空 | 直接不弹；未知尺寸块按二进制块处理（见下） |
| B1-b | 未知尺寸块**已存在 ignored.txt** | DecompressBlock 2989 `!ignoredSizes.Contains(num)` | 静默不弹，按二进制块处理 |
| B1-c | `sizes` 为空（像素数质数/因数 >6000 / 空块）→ `tbSize.Maximum=-1` 抛异常 | InitSizes 4406-4472；DecompressBlock 3000-3017 `catch { ignoredSizes.Add(num); }` | **对话框不出现**，且该尺寸**被永久加入 ignored.txt**（下次也不再询问）——这正是"有时不出现"的最可能原因之一；且不可恢复（用户必须手删 ignored.txt） |
| B1-d | `SetBuffer`/`ShowDialog` 期间任何异常 | DecompressBlock 3000-3017 同上 | 同上 |
| B1-e | `ShowDialog(owner)` 打开后主窗体被禁用，预览解码大图导致 UI 卡死数十秒 | tbSize_ValueChanged 4544（每次 ValueChanged 全量解码+逐像素） | 表现为"窗口假死/不绘制"，用户以为没弹 |
| B1-f | 用户直接关闭对话框（X/取消） | DecompressBlock 3003-3011 | 该尺寸记入 ignored.txt（**下次不再询问**），块按二进制保存 |
| B1-g | `LogoProject.BootLogoToImage` 在 CLI（owner==null）遇未知尺寸 | 3646-3675 `ErrorCode=7; throw` | "打开第二屏"在 GUI 下不会（owner 非空），CLI 的 `bl2img` 会失败 |

> **与 docs/spec_original.md B1 的差异**：spec 写"异步/异常路径下 owner 可能为 null"。实际 `dialogOwner` 只在 `LogoProject.Init` 赋值一次，GUI（MainForm 构造）传入 `this`，**此后永不为 null**；异步/异常路径不会把它置空。B1 的 GUI 可复现根因是 **B1-c 的尺寸候选枚举失败 + 异常被吞后写入 ignored.txt**（以及 B1-e 的 UI 卡死观感）。**【证据：2106/2110-2115；全文件无其它对 dialogOwner 的赋值（grep 2112 之外无 stsfld 到该字段）】**

### 4.4 布局（`InitializeComponent` 4665–4744 + `ra_LogoBuilder.SizeDialog.resources`）

- Form：ClientSize 392×464；MinimumSize 201×232；StartPosition=CenterParent；FormBorderStyle=SizableToolWindow；SizeGripStyle=Show；AutoScaleMode=Font(6,13)；AcceptButton=btnOk；CancelButton=btnCancel；Text="Image size"/"图像大小"。
- `lblSize`：(12,9)，AutoSize，Font=Microsoft Sans Serif 16pt，初始 "label1"（运行时被替换）。
- `pictureBox1`：(12,38)，287×414，Anchor=四边，SizeMode=Zoom（显示 buf 解码结果）。
- `tbSize`：(314,38)，45×356，**Vertical**，TickStyle=Both，SmallChange=1（4689-4695），Anchor=Top,Bottom,Right。
- `cbColorDepths`：(259,12)，121×21，DropDownList，Anchor=Top,Right。
- `btnOk`：(305,400) 75×23 "OK"；`btnCancel`：(305,429) 75×23 "Cancel"。
- 说明：**预览尺寸 287×414 是固定框**，以 Zoom 模式显示；不显示"实际尺寸"文字以外的信息。

---

## 5. 图像预览控件 `ImagePreviewControl`（1381–1773）

- 构造函数 `ImagePreviewControl(LogoSize defaultImageSize, string imagePath)`（1514–1520）：保存默认尺寸/路径；`InitializeComponent`。
- 布局（1740–1773）：UserControl Size 76×155；内部 `pictureBox1` 76×135（Anchor 四边，SizeMode 由 RefreshImage 设置）；`lblSize` (0,138) 76×16，TextAlign=TopCenter，字体默认，**点击 lblSize 触发 btnEditSize_Click**；`lblSize` 初始指向资源串 "label1"。
- `RefreshImage()`（1521–1567）：
  1. `Image image = LogoProject.ImageFromFile(imagePath)`（`Image.FromFile`，**不锁文件**吗？FromFile 会锁文件直到 Dispose；此处未 Dispose → 外部替换同名文件后旧句柄仍在，可能引发 FileSystemWatcher 刷新时 IOException/旧图残留 —— 新缺陷 N3）
  2. `pictureBox1.SizeMode=Zoom; pictureBox1.Image=image;`
  3. `lblSize.Text = string.Format("{0} x {1}", image.Width, image.Height)`（偏移 227）
  4. 若 `image.Size != DefaultImageSize.Size` → `lblSize.ForeColor = Color.Red`（否则默认色）
  5. `Error=false`；成功返回 true
  6. 异常：`SizeMode=CenterImage`；`pictureBox1.Image=Properties.Resources.error`（32×32 红叉位图）；`Error=true`；`ErrorMessage=ex.Message`；返回 false
- `Error` 属性 setter（1395–1443）：若 Error 为 true：`lblSize.Text = rLoc.GetString("error") ?? "Error"`（偏移 246/257），`lblSize.ForeColor=Red`；否则清空文字。
- `pictureBox1_MouseClick`（1566–1616）：**右键**且文件存在 → `ShellContextMenu.ShowContextMenu(Handle, [FileInfo], e.Location)`；异常弹 MessageBox(error/Error)。
- `btnEditSize_Click`（1618–1692）：文件不存在直接 return；否则读图 → `new SizeDialog(); SetBuffer(LogoProject.ImageToArray(image, DefaultImageSize.ColorDepth), DefaultImageSize)` → OK 后 `if (DefaultImageSizeChanged != null) DefaultImageSizeChanged(this, new SizeEventArgs(dlg.SelectedSize))`；异常弹 MessageBox。
  - 注意：`ImageToArray(image, DefaultImageSize.ColorDepth)`（2499-2541）会按当前位深重编码原图，然后弹尺寸对话框。
- `pictureBox1_DoubleClick`（1694–1705）：`Process.Start(imagePath)` —— **用系统默认看图程序打开原图**（不是内嵌编辑器）。异常弹 MessageBox。
- **无拖放、无键盘交互**。**【证据】**

---

## 6. 工程逻辑 `LogoProject`（2072–4010）

### 6.1 常量/字段（2074–2108）

`HeaderSize=512`、`UpdaterScriptName="updater-script"`、`UpdateBinaryName="update-binary"`、`ImgNameFormat="img{0}.png"`、`BlockNameFormat="block{0}.dat"`、`ImagePrefix="image:"`、`BlockPrefix="binary:"`、`SizesFileName="sizes.txt"`、`IgnoredFileName="ignored.txt"`；`sizesDict:Dictionary<int,LogoSize>`（key=W*H*ColorDepth）、`ignoredSizes:List<int>`、`ProjectPath`、`header:byte[]`、`BlockList:List<string>`、`dlgImageSize:SizeDialog`（静态单例）、`rLoc`、`dialogOwner`、`ErrorCode`。**【证据】**

### 6.2 `Clear` / `Init`（2110–2121）

`Init(rMgr, owner)`：`LoadSizes(); rLoc=rMgr; dialogOwner=owner;`
`Clear()`：`ProjectPath=null; BlockList.Clear();` —— **不清 header/sizesDict**。再次 OpenProject 会覆盖 header；但 Clear 后直接 PackProject 会用**上一个项目的 header**（若 OpenProject 失败后误点打包）→ 潜在串项目缺陷（新缺陷 N6，实际 btnPack 需先 RefreshImages 有图才会执行，风险较低）。**【证据】**

### 6.3 解包 `CreateProject`（2677–2733）

1. `BlockList.Clear();`
2. `CreateProjectReadHeader`（2735–2760）：读 512 字节 header 并**先写入项目目录 `header.bin`**；`Seek(512)`；读 `dataLen`；若 `dataLen > reader.BaseStream.Length - 512` → `ErrorCode=3; throw(dataLengthError1)`（偏移 455）；`Seek(512)`。
3. `CreateProjectExtractImages`（2762–2857）：读 `count`；读 `dataLen2`，`!= dataLen` → `ErrorCode=3; throw(dataLengthError2)`（488）；`new int[count]` 读 offsets（**未限制 count 上限，count 巨大直接 OOM/内存不足** —— B8 证据）；逐块：`Seek(512+offset)`；长度 = 下一 offset 差（最后块 = dataLen-offset）；`DecompressBlock(new MemoryStream(ReadBytes(len)))`。
4. `SaveProjectFile(); CopyUpdateFilesFromProgramToProject();` → return true。
5. 异常：`ProjectPath=null; if(ErrorCode==0) ErrorCode=6; throw;`。
- **注意**：`header.bin` 在解析失败时已被写入/覆盖（步骤 2 先写盘），留下半个项目目录。**【证据 2737-2738】**

### 6.4 块分类 `DecompressBlock`（2971–3028）

```
array = DecompressStream(stream)          // Ionic.Zlib ZlibStream 解压（3595-3638 的逆过程）
num = array.Length
if (!sizesDict.ContainsKey(num)) {
    if (!ignoredSizes.Contains(num) && dialogOwner != null) {
        try { dlgImageSize.SetBuffer(array, LogoSize.Empty);
              if (dlgImageSize.ShowDialog(dialogOwner) == OK) sizesDict[num] = dlgImageSize.SelectedSize;
              else ignoredSizes.Add(num); }
        catch { ignoredSizes.Add(num); }
    }
}
if (sizesDict.ContainsKey(num)) AddImageBlock(array); else AddBinaryBlock(array);
```
（**若 `dialogOwner==null` 且未知尺寸**：既不询问也不记 ignored → 直接 AddBinaryBlock。）
- `AddImageBlock`（3030–3084）：`size = sizesDict[len]`；`StreamToImage(new BinaryReader(new MemoryStream(array)), size)`；**bitmap==null 时静默 return（不记录块！）**；否则 `name = string.Format("img{0}.png", BlockList.Count+1)`（594）；`BlockList.Add(string.Format("{0}{1} {2} {3} {4}", "image:", name, W, H, depth))`（615/652）；`bitmap.Save(ProjectPath+name, ImageFormat.Png)`。
  - **块顺序风险**：文件名/块序号用 `BlockList.Count+1`，因此**只要有一块被跳过（如上面 bitmap==null）后续编号就整体错位**，.project 里的 imgN 与真实块顺序不再对应（B9/N2 证据）。
- `AddBinaryBlock`（3086–3091）：`name = string.Format("block{0}.dat", BlockList.Count+1)`（665）；`File.WriteAllBytes`；`BlockList.Add("binary:"+name)`（690）。

### 6.5 保存/打开项目

- `SaveProjectFile`（2859–2931）：`FileMode.Create,Write,Share.Read` 写 `.project`（521），逐行 BlockList。
- `CopyUpdateFilesFromProgramToProject`（2933–2969）：从 **exe 目录**若有 `updater-script`/`update-binary`，`File.Copy(src, projectDir/name, overwrite:true)`。**【证据：字符串 538/567】**
- `OpenProject(path)`（3098–3178）：`header.bin` 存在且**长度必须恰好 512** 才接受（否则 return false，不报错）；`.project` 存在；逐行读入 BlockList；`ProjectPath=path`。
  - **OpenProject 不校验 `.project` 内容**（任意垃圾行都会进 BlockList，后续 FillPictureBoxesList/CompressBlocks 才可能崩）。**【证据】**

### 6.6 打包 `PackProject`（3180–3266）

1. `list = CompressBlocks(out num, progress)`（**先压缩再检查空列表**）；
2. `if (BlockList.Count==0) { ErrorCode=4; throw(noImages); }`（偏移 705）；
3. 目标 `logoPath` 文件已存在 → `File.Delete`（3190-3193）；`File.Create`；
4. 写 `header[0..4)`（原 magic）、`num`(dataLen)、`header[8..512)`（504 字节）、`count`、`num`、offsets（`8+4*count+累计`）、各块压缩字节。
   - **不校验 header 长度**：若 header 为 null（未打开项目就打包，CLI 可能）或长度 <512，`header,8,504` 抛 ArgumentException（N5，退出码 2，无友好提示）。**【证据：3198-3202】**
   - **不校验 count/offset 上界**；`dataLen` 完全由数据算得，不校验 `dataLen <= 原分区大小`。**【证据】**
- `CompressBlocks`（3390–3474）：对每个 BlockList 行：`StartsWith("image:")`(652) → `CompressImage(ProjectPath+ExtractImageName(line), ExtractColorDepth(line))`；否则 `binary:` → 读 `ProjectPath+line.Substring(len("binary:"))` 原字节。`num += array.Length`；`dataLength = 8+4*count+num`。
- `CompressImage`（3539–3593）：`Image.FromFile` → `ImageToStream(bw, image, colorDepth)`（2543–2645：`ImageToArray` 同构——取 32bppArgb 位图、LockBits、逐像素：depth2 → `IntToColor16(int)` 写 ushort；depth4 → 写 int；**不按 .project 的 W/H 缩放**，尺寸不符会在读完位图后产生比预期多/少的字节）→ `CompressStream`（Ionic.Zlib `ZlibStream(..., CompressionLevel.BestCompression)`，3595–3638）。
  - **尺寸校验缺失**：`imageSizeError`（偏移 4705）字符串存在，但**打包路径从不使用它**——读代码未见 W/H 校验；尺寸不符会导致生成的块长度≠解压长度（下次解包按二进制块处理）或位图 stride 越界异常。**【证据：字符串 usages 中 imageSizeError 无调用点，见 string_usages.tsv】**
- `ExtractColorDepth(line)`（3476–3527）：`Split(' ')`；`array.Length>3 && int.TryParse(array[3])` 且 ∈{2,4} → 返回；否则 **2**。
- `ExtractImageName(line)`（3529–3537）：`Substring(len("image:"))` 后 `Split(' ')[0]`。
- `MakeUpdateZip(logoPath,resultZipPath)`（3268–3304）：`MakeZip(...)`；成功后 `MessageBox.Show(string.Format(takeIt ?? "Take your update.zip in {0} :)", ProjectPath), ...)`（对话框 owner 为 dialogOwner，CLI 下为 null → **不弹提示**）。
- `MakeZip`（3306–3367）：`new ZipFile(typeof(LogoProject).Assembly.GetManifestResourceStream("LogoBuilder.update.zip"))`（偏移 796；Crypto Obfuscator 会把该名字映射到内嵌 ResourceAssembly 的资源）；`zip.AddFile(logoPath, "")`；若项目目录有 `updater-script` → `zip.AddFile(script, "META-INF\\com\\google\\android")`（偏移 926 **反斜杠路径**！）；`update-binary` 同理；`SaveZip`（存在则先 `File.Delete`，再 `zip.Save`）。
  - **反斜杠目录名**：Ionic.Zip 可能把 `META-INF\com\google\android` 原样写入 zip entry，部分 Recovery 对 `\` 路径不兼容（N7，需实测；原版模板 zip 内路径本就是 `/`，覆盖写可能产生混合分隔符）。**【证据：字符串 841 用反斜杠，926 目录串反斜杠；spec 4 假定 `/`】**
  - `SaveZip` 先删目标再存：中途失败会丢失已有 update.zip。**【证据 3369-3388】**

### 6.7 错误码表

| ErrorCode | 设置位置 | 含义（对应资源键） |
|---|---|---|
| 0 | 默认 | 成功 |
| 3 | 2753 / 2777 | 数据长度校验失败（dataLengthError1/2）|
| 4 | 3186 | BlockList 为空（noImages）|
| 6 | 2729 | CreateProject 其它异常（catch 兜底）|
| 7 | 3673 | BootLogoToImage：CLI 下无法确定尺寸（"Unknown image size."，偏移 1000）|

（CLI 退出码 = ErrorCode；未识别命令=1；异常且 ErrorCode=0 → 2。）

### 6.8 第二屏（boot_logo）

- `ImageToBootLogo(image, fileName, progress)`（3720–3759）：`File.Create`；`ImageToStream(bw, image, **2**, progress)` —— **固定 RGB565、无任何文件头**、无尺寸记录。**【证据】**
- `BootLogoToImage(fileName)`（3640–3718）：`length = stream.Length`；若 `sizesDict` 有 length → 用该 LogoSize 解码；否则 `owner != null`：`dlgImageSize.SetBuffer(File.ReadAllBytes(file), LogoSize.Empty)` + ShowDialog，取消→ throw `cancelled`（偏移 981），OK→`sizesDict[length]=SelectedSize`；`owner == null`：`ErrorCode=7; throw "Unknown image size."`（1000）。随后 `StreamToImage`。
  - **不校验长度是否为 W*H*2 的精确倍数**；不校验文件大小上限。**【证据】**

### 6.9 像素转换

- `StreamToImage(BinaryReader, LogoSize)`（2449–2497）：`new Bitmap(w,h)`（默认 32bppArgb）→ LockBits WriteOnly → `num = stride*Height/4` 个 int；depth2：`array[i]=Color16ToInt(reader.ReadUInt16())`；depth4：`array[i]=reader.ReadInt32()`；Marshal.Copy 回位图。
  - **无 EOF 检查**：数据不足 `reader` 抛 EndOfStreamException；数据多于 W*H 的字节被忽略（不报错）。**【证据】**
- `Color16ToInt(ushort)`（2433–2439）：`b=(s&0x1F)/32.0*256`, `g=((s>>5)&0x3F)/64.0*256`, `r=(s>>11)/32.0*256`，返回 `0xFF000000|r<<16|g<<8|b`。
  - **数值缺陷**：R/B 通道最大值 = 31/32*256 = **248**（不是 255），G = 63/64*256 = **252**；即 RGB565 解码永远到不了纯白 255，且各通道有量化偏差。重建版应按 `(v*255+15)/31` 等标准位运算（N8）。**【证据】**
- `IntToColor16(int)`（2441–2447）：`(byte)r/256*32`, `(byte)g/256*64`, `(byte)b/256*32` 取整后移位（`\u0015\u0005`=Convert.ToUInt16 / 四舍五入不可知）。配合上面编码 → 往返有损。**【证据】**
- `LooksLike32Bit(byte[])`（3860–3949）：null / 非 4 倍数 → false；逐 u32 读，**任一像素 < 0xFF000000 即 false**；全 alpha=255 才 true。
  - **缺陷**：带透明/半透明像素的 32bpp 图会被判为 16bpp；且 RGB565 数据按 4 字节分组"恰好"全部高位为 FF 的概率极低，误判方向基本是"32bpp→16bpp"（B7 证据）。**【证据】**
- `FindNearestRatioForBufSize(sizeMult, defaultRatio=0.5625)`（3951–4009）：遍历 sizesDict，取 `|sizeMult - W*H|` 最小项的 `W/H`；相等即提前 break；空字典→0.5625。
  - **缺陷**：当多个尺寸像素数相同（W*H=同值，如旋转 540x960 vs 960x540）时只保留**先插入**项的比例，结果依赖 sizes.txt 行序。**【证据】**

### 6.10 改尺寸 `ChangeImageSize`（3761–3858）

`blockInd` → 读当前 img 文件 → `ImageToStream(bw, Image.FromFile(path), ExtractColorDepth(BlockList[blockInd]))` → 用 `StreamToImage(...newSize)` 重新解码 → `bitmap.Save(path, PNG)`；更新 BlockList 行（offset 615/594：`image:img{n}.png W H depth`）；`SaveProjectFile()`；若 `addSizeToDefaults` → `sizesDict[newSize.Key]=newSize`（存在则覆盖）。
- **不检查 newSize.Key 与像素数是否一致**（newSize 来自 SizeDialog 的因数对，恒一致；但此 API 若被其它路径调用可传任意值）。
- **写盘顺序**：先写 PNG，再 SaveProjectFile；任一步失败会留下不一致项目（B9 证据）。**【证据】**

---

## 7. Tips.dll（`analysis/tools/Tips.decompiled.cs`，行号即该文件行号）

- 独立程序集 `Tips.dll`（23,040 B，.NET 4.0，Form + 静态管理器），**由主程序反射/程序集方式加载**：MainForm.InitTips 只检查 `exeDir + "Tips.dll"` 是否存在来决定 `btnTips.Visible`（5067–5075，偏移 1549）；按钮点击/FormShown 调用 `Tips.TipsMain.Show/StartUp(this)`（string_usages 无 "Tips.dll" 相关解密调用 → 主程序通过 `Type.GetType`/`Assembly.Load` 代理调用，见 call_resolution 未列出的 A-proxy）。**【推断：加载方式为反射，字符串池只有 "Tips.dll" 一个标识】**
- `TipsMain` 静态构造（Tips.decompiled.cs 259–271）：从 **Tips.dll 所在目录** 读 tips：
  - `FindTips`（273–287）：`CurrentUICulture` 起，逐级 `Parent` 找 `<Tips.dll目录>\<culture.Name>\tips.txt`，找到即用；最后回退 `<Tips.dll目录>\tips.txt`。**逐行**即每条提示。
  - 找到文件则 `loaded=true`，否则 Tips 窗口永不显示、按钮点也无反应。
- `TipsForm`（114–250）：布局 FixedToolWindow 风格可调整大小，ClientSize 430×225，MinimumSize 404×232，StartPosition=CenterParent；`lblTitle`="Did you know ...?"（14.25pt）；`lblTip` 只读文本区 (78,46) 340×117，Anchor 四边，支持换行滚动吗？——Label 无滚动条，长 tips（中文版非常长）**会被裁剪/溢出**（N9：中文 tips.txt 每行文本远超 340×117 可显示范围，Label 不换行滚动 → 显示不全）。**【证据：资源尺寸 + Label 无 AutoScroll】**
- 文本转义（165–169）：`Unescape` 把字面 `\r\n`→换行、`\n`→换行、`\t`→制表符。**这就是 tips.txt 中出现字面 `\n` 的原因**（文件里是反斜杠 n，运行时才转换）。
- 行为（144–185）：`NextTip` = 上次编号+1，越界回 0，`SetTipNo` 立即 `Settings.Default.Save()`；`PreviousTip` = -1，<0 时回最后一条；`cbShowAtStartUp.Checked` 初值 = `Settings.Default.showAtStartUp`；关闭时保存。设置文件 `Tips.dll.config` 默认 `lastTipNo=-1`、`showAtStartUp=True`（user.config 同 .NET 规则）。
- `StartUp(owner)`（289–295）：`loaded && showAtStartUp` → `Show(owner)`（**ShowDialog 模态**，MainForm 构造完成后 FormShown 时弹出 → 每次启动强制看提示，可取消勾选）。

---

## 8. 作者/捐赠控件 `SignControl`（8179–8355）

- UserControl Size 74×37；`lblBy.Text="by"`（8.25pt）；`lblEmail.Text="kadan"`（MediumBlue、Hand 光标、下划线？资源无）；`pictureBox1` 74×21 8bpp 位图（捐赠按钮图）；ToolTip：`pictureBox1.ToolTip="Was the program helpful for You?"`、`lblEmail.ToolTip="Email me (ankadnikov@gmail.com)"`。
- `label2_Click`（8283–8293，绑定到 lblEmail.Click）：`Process.Start("mailto:ankadnikov@gmail.com?subject=" + EmailTheme)`（偏移 3568；`string.Concat`）。
- `pictureBox1_Click`（8295–8330）：若 `CurrentUICulture.Name` **不以 "ru" 开头** → PayPal USD 链接（偏移 3641），否则 RUB 链接（4077）；`Process.Start`；异常弹 MessageBox。
- `EmailTheme` 属性（get/set，0x06000154/0x06000155）：默认空串，**主程序未设置 → subject 为空**（"[no subject]"）。**【证据】**

---

## 9. 缺陷清单（B1–B10 证据加深 + 新缺陷 N1–N10）

> B1–B10 编号沿用 `docs/spec_original.md` §5.2；每条给出本次新增的**代码级**证据。

| # | 结论（spec 原有） | 代码证据（行号） | 本次加深/修正 |
|---|---|---|---|
| **B1** | 分辨率选择窗口有时不出现 | 2102 静态单例；2110-2115 owner；2971-3028 DecompressBlock；4406-4472 InitSizes；4339-4404 SetBuffer；4544 预览解码 | **修正 spec 根因**：不是 owner 异步置空（GUI 恒非空）。真实路径：①空/质数像素数 → `tbSize.Maximum=-1` 异常被 3013-3017 吞掉并写入 ignored.txt（永久不再问）；②拖动时全量解码导致假死；③取消/X 也写入 ignored.txt。CLI 才可能 owner==null |
| **B2** | 目录选择经常出问题 | MainForm.CreateFolderBrowserDialog 5343-5357；btnCreate 5266；btnOpen 5377；Ionic FolderBrowserDialogEx 68-791 | 自带 `FolderBrowserDialogEx`：`new-style` + `ShowNewFolderButton`、`SHBrowseForFolder`；**不校验可写性/非空/是否已有项目**；失败仅 EOF/异常提示；`SelectedPath` 为空时直接当项目目录（`CreateProject` 会在此路径写 header.bin 失败→"Can't create project"）|
| **B3** | 无法在软件内替换图片 | ImagePreviewControl.btnEditSize_Click 1618-1692；DoubleClick 1694；MainForm FileSystemWatcher 5467-5556 | 双击 = 用系统看图器打开；"改尺寸"入口藏在小尺寸文字 `lblSize`（76×16px，无提示）；**没有"选择新图片替换当前块"的流程**；唯一"编辑"是改尺寸（ChangeImageSize）|
| **B4** | 只识别 bin 格式 | ofdLogoBin.Filter（资源："logo.bin|logo.bin|logo.img|logo.img|所有文件"）；CreateProjectReadHeader 2735-2760 | `.img` 只是过滤器文案，解析仍强校验 512 头 + dataLen + dataLen2；**头不符 → dataLengthError1（"数据长度错误(第一种情况)"），信息不可读且不说原因**；无 magic 校验（头 4 字节是什么都接受）|
| **B5** | 只能出卡刷包 | btnPack 5614-5721；MakeUpdateZip 3268；MakeZip 3306 | 输出固定 `<project>\logo.bin` + `<project>\update.zip`（无另存为、无分区对齐、无单独 .img）；`SaveZip` 先删旧文件（中断即丢旧包）|
| **B6** | 无法自动识别分辨率 | DecompressBlock 2971-3028；FindNearestRatioForBufSize 3951-4009；Ignored 机制 | 只有 sizes.txt（key=W*H*D）精确匹配 + SizeDialog 让用户枚举因数对；**"取消即永久忽略该解压长度"** 机制使问题更难发现；sizes.txt 合并（内置+程序目录）在 LoadSizes 2248 |
| **B7** | 32bpp 误判 | LooksLike32Bit 3860-3949 | 判据 = 全部像素 alpha==0xFF；**任一透明像素即误判 16bpp**；且 16bpp 数据按 u32 分组时"恰好全 ≥0xFF000000"概率极低（反向误判基本不可能）；另 `Color16ToInt` 量程 248/252 导致颜色偏暗（N8）|
| **B8** | 异常输入不健壮 | 2735-2760（dataLen>len-512 才查）；2762-2857（`new int[count]` 无上限；不校验 offset 单调/越界；zlib 异常未捕获→冒泡）；2830-2853 | **未校验**：magic、offset[0]==8+4*count、offsets 单调、offsets[i]<dataLen、`dataLen==8+4*count+Σ压缩块`；`ReadBytes(len)` len 负数/超大。错误提示只有两个不通用的定值串（455/488）|
| **B9** | 项目/图片一致性靠用户 | AddImageBlock 3030-3084（`BlockList.Count+1` 编号、bitmap==null 静默跳过）；ChangeImageSize 3761-3858（先写 PNG 后写 .project）；OpenProject 3098-3178（不校验 .project 内容）| 只要一块被跳过，**后续 imgN/blockN 编号整体错位**，.project 与文件不再对应；`bitmap==null` 分支不记录任何块（块数变少，打包结构直接错位）|
| **B10** | 仅 Windows / 无 HiDPI | MainForm/SizeDialog `AutoScaleMode.Font` + 固定像素 Layout（6089-6216/4665-4744）；无 DPI API 调用 | .NET4.0 WinForms 默认系统 DPI 虚拟化；固定 787×445/392×464 等；**高 DPI 下 150%/200% 会整体模糊或控件错位**；tips 长文本溢出（N9 同因）|

### 新发现缺陷

| # | 严重度 | 缺陷 | 证据 |
|---|---|---|---|
| **N1** | 高 | **user.config 中 `LastColorDepthIndex` 越界会让整个程序启动即崩**：`LogoProject` 静态构造 `new SizeDialog()` → `InitColorDepths` 里 `cbColorDepths.SelectedIndex = Settings.Default.LastColorDepthIndex` 无范围检查 → ArgumentOutOfRangeException → `TypeInitializationException`（MainForm 构造调用 `LogoProject.Init` 时触发）→ 每次启动必崩，普通用户无法恢复（需手删 user.config） | 2102；4322-4337；`ApplicationSettingsBase` 无范围校验 |
| **N2** | 高 | 块编号错位（见 B9）：块被静默跳过时 `imgN.png` 与块顺序错位，**打包后 logo.bin 结构错乱却无任何提示** | 3060-3063（bitmap null return）；3071；3088 |
| **N3** | 中 | `Image.FromFile` 打开的图片**从不 Dispose**（RefreshImage 1523-1532、CompressImage 3541、MakeBootLogo 5900、boot 图 6045）→ 文件句柄泄漏；外部改图后 FileSystemWatcher 刷新可能读到旧句柄/占用文件 | 1521-1567；3539-3593；5895-6005；6028-6077 |
| **N4** | 中 | 主窗体滚轮步长错误：`-e.Delta*70`（Delta=±120）→ 一次滚轮约 8400px，被 clamp → 直接跳到底/顶 | 5776-5787 |
| **N5** | 中 | `PackProject` 对 `header==null` 或长度≠512 无校验：CLI `pack` 在坏项目上抛 ArgumentException/NullReferenceException，退出码 2 且无中文提示 | 3194-3202；RunArgs catch |
| **N6** | 低 | `Clear()` 不清 `header`：打开失败/新建失败后残留上一项目 header，若随后打包（需先有图）会用错 header | 2117-2121 |
| **N7** | 中 | update.zip 内自定义文件的目录用**反斜杠** `META-INF\com\google\android`，与模板原有 `/` 分隔符混用；部分 recovery 可能不识别 | 3327-3328/3342/3356（字符串 926） |
| **N8** | 中 | RGB565 转换量程错误：解码到 248/252 而非 255；编码用 `/256*32` 有系统性偏暗/偏色，往返不保真 | 2433-2447 |
| **N9** | 中 | 尺寸不符提示 `imageSizeError` 是死字符串（无调用点）；打包实际不做尺寸校验，尺寸不符时可能生成无效块或越界异常 | `string_usages.tsv` 中 offset 4705 仅定义无使用；3539-3593 |
| **N10** | 低 | `ignored.txt` 只增不减且不去重；`sizes.txt` 写回顺序 = Dictionary 枚举序（不稳定），版本间 diff 噪音大 | 2205-2212；2364-2379；2406-2412 |
| **N11** | 低 | `MainForm_DragEnter/DragDrop` 只接受**恰好 1 个**文件；多选拖放被忽略且无提示 | 5811-5815；5864-5868 |
| **N12** | 低 | `btnCreate_Click` 先弹"选 bin"再弹"选目录"，且目录对话框是**先创建后使用**（对象创建在文件对话框之前）；用户取消的两种时序提示不一致（先取消文件→静默；先取消目录→静默） | 5266-5282 |
| **N13** | 低 | `SizeDialog` 全程序唯一且从不 Dispose；关闭用 X/取消后 `buf` 仍引用上一块大数组（内存驻留，2MP≈8MB） | 2102；4339-4404 |

---

## 10. 与 `docs/spec_original.md` 的矛盾/需要更新的点

| spec 位置 | spec 表述 | 实际（证据） |
|---|---|---|
| §5.1-5 | "打包 → 生成新的 logo.bin（保存对话框）" | **无保存对话框**：`btnPack_Click` 固定写 `<项目目录>\logo.bin` 与 `<项目目录>\update.zip`（5633/5674）|
| §5.1-8 | "设置保存在 `%APPDATA%` 下程序配置文件（原版保存 sizes/ignored/language/窗口位置）" | **不对**：sizes.txt/ignored.txt 写在 **exe 目录**（SaveSettings 2125-2128/2199）；Last* 设置走 .NET `ApplicationSettingsBase` → `%LOCALAPPDATA%\LogoBuilder\...\user.config`；**没有语言设置持久化**（语言只来自命令行参数）；**没有窗口位置持久化**（Form 无 Location 保存代码）|
| §5.2 B1 | "异步/异常路径下 owner 可能为 null" | GUI 路径 owner 恒为 MainForm（2114 在 ctor 里传 this；无其它赋值）；CLI 才 null。真实 GUI 根因见 §4.3 B1-c/B1-e |
| §5.2 B4 | "OpenFileDialog 过滤器只有 logo.bin/logo.img" | 过滤器文案确实有 `logo.bin`/`logo.img` 两个 pattern + 所有文件；**程序不校验 magic**，只是头/dataLen 校验失败时报 dataLengthError1 |
| §5.2 B6 | "解压块无尺寸元数据……没有基于宽高比/stride/相邻块一致性的推断" | 正确；补充：原版有 `FindNearestRatioForBufSize`——用 sizes.txt 中"最接近像素数"项的宽高比给 SizeDialog 选初始候选，这是唯一的"启发式" |
| §4 update.zip | "若项目目录存在 updater-script/update-binary，用它们覆盖模板中的同名路径" | 正确；补充：目录串用 `META-INF\com\google\android` 反斜杠；`logo.bin` 固定根目录名；`MakeUpdateZip` 成功提示仅 GUI 弹（CLI 不弹）|
| §2 项目目录 | "创建项目时从 logo.bin 解包为 header.bin/.project/imgN.png/blockN.dat/sizes.txt/ignored.txt" | 正确；补充：**sizes.txt/ignored.txt 不会复制进项目目录**（只有 header.bin/.project/imgN/blockN/可选 updater 文件）；sizes/ignored 始终在 exe 目录。`.project` 行格式 `image:img1.png 540 960 4` / `binary:block5.dat` 与 spec 一致 |
| §1.1 文件布局 | "header[0x10..0x1FF] = 0x00 + 0xFF 填充（原样保留）" | 原版只保留 8..512 原样；magic 前 4 字节原样写回，dataLen 重算。**tag 不校验**（任何 tag 都可打开）|
| §1.2 像素格式 RGB565 | "各通道线性放大 32/64→256" | 数值实为 /32.0*256（max 248）与 /64.0*256（max 252），**不是** 32→256 的标准映射（N8）|
| §5.2 B7 | "32bpp 检测 LooksLike32Bit 用'每 4 字节最高位=0xFF'启发" | 准确表述：把 buffer 按 u32 LE 读取并**逐个**要求 `>= 0xFF000000`（全 alpha=255 才判 32bpp）|
| §6 重建目标-5 | "输出……update.zip（兼容 CWM/原版模板，可自定义脚本）" | 已确认模板/脚本行为；**原版没有签名校验/对齐/分区大小检查** |

---

## 11. 对重建版 GUI 的复用建议（行为映射）

| 原版行为 | 重建版建议 | 理由 |
|---|---|---|
| 主窗体顶部一排 7 按钮 + 下方缩略图流 + 底部进度/打包按钮 | 保留同样信息架构（新建/打开/资源管理器/打包/第二屏→图片/图片→第二屏/提示/关于+捐赠位），但用 ttk 布局自适应窗口 | 用户熟悉；B10 修复 |
| 双击缩略图 = 系统看图器 | 改为**双击选择替换图片**（spec 目标 4），右键菜单保留"用系统打开" | 修复 B3 |
| lblSize 小字点击 = 改尺寸 | 缩略图下方放显式"尺寸/位深"按钮 + tooltip | 可发现性 |
| 未知尺寸弹 SizeDialog（因数对 + 位深 + 预览） | 保留"候选列表"交互，但：候选加 score/来源、可自定义 W×H、记住选择；**绝不能静默把用户取消记入永久忽略**（或在 UI 提供 ignored 管理） | 修复 B1/B6 |
| sizes.txt/ignored.txt 在 exe 目录 | 改为用户配置目录（并支持导入原版 sizes.txt） | 修复 B2/权限 |
| 打包固定输出项目目录 logo.bin/update.zip | 增"另存为"、单文件 logo.img、可选分区对齐/零填充；update.zip 可自定义脚本（默认原模板） | 修复 B5 + spec §6 |
| 块顺序/编号：`image:imgN.png W H D`（N 连续且=块序） | 采用 project.json（顺序显式），并**导入兼容原版 .project**（顺序即块序，N 仅文件名） | 修复 B9/N2 |
| update.zip 模板 = 原版 CWM 模板（logo.bin→/dev/logo，190KB update-binary，JAR 签名） | 直接复用 `analysis/resources/template_*` 与 `ra_LogoBuilder.update.zip`；路径用 `/` 分隔 | 行为兼容 |
| 256 色/16 位图形界面、无 HiDPI | ttkbootstrap 主题 + tk scaling；中英双语 | B10 |
| 语言选择靠命令行参数 | 设置里提供语言切换，同时保留 `--lang` CLI | parity + 易用 |
| 错误提示直接 `ex.Message` | 统一错误文案 + hint + 上下文 | B8 |

---

## 12. 未决项（静态无法确认）

1. `A.\n::\u0008` 解码器对 1,977 B 资源给出 193,536 B 结果的确切内部路径（DES 解密后的 1,953 B 原始 Deflate 流只解出 5,195 B 字符串表；解码器报告的 193,536 B 与"字符串表+打包资源"一致，但 Deflate 何以膨胀到该长度未在 IL 层完全验证）。不影响字符串恢复（137/137 回填一致）。
2. `ResourceAssembly`（内嵌 PE）的加载时机：主程序通过 Crypto Obfuscator 的 patched `GetManifestResourceStream` 读取 `LogoBuilder.update.zip`/`*.resources`；具体注入的 `A.*` 初始化顺序未逐条追踪。
3. `Ionic.Zip` 对 `META-INF\com\google\android`（反斜杠）实际写出的 zip entry 名（N7）需实际打包验证（本任务不做动态执行）。
4. `tips.txt` 中文版的行数/显示效果（N9）未在 96DPI 下实测；仅根据 Label 尺寸推断会被裁剪。
