# 制作接口

普通新制作只调用本接口，整个制作和交付过程都不读脚本、模板或网页源码。低层源码仅用于用户明确要求的技能维护/源码调试。交付时也只调用deliver并读对应接口说明，不研究实现；不提前读交付文档，不现场编写裁图、哈希、归档、排版或复制脚本。输入错误按返回字段修正；脚本自身异常使用已文档化恢复方式，无法恢复则报告具体故障，不自行转入维护。

Windows入口：`& "<技能目录>/scripts/sheet_flow.ps1" <命令> --root "<工作目录>" ...`。其他系统用可用Python运行 `scripts/sheet_flow.py`，其参数相同。入口自动选择已提供的运行时，不反复试失效的默认python。所有JSON文件为UTF-8；相对图片和config路径均按root解析，绝对路径原样使用；接口返回的配置及提交命令使用绝对路径，不依赖当前目录。阶段输入的真实坐标按各自原图像素填写。

每次调用都返回`next_action`及下一步所需参数/文件，直接执行下一步，不另跑status、重新列目录或重读提示词。仅恢复被中断的任务时用status，它只读、不新增调用。没有版本变化或具体未解决疑点时，不重复打开已清楚的同一文档或图片；独立复核者须首次亲自看必要证据。模型实际调用图像工具，脚本不调用模型、不判断视觉。生成返回tool_call_count及requests，本批每项只调用一次；首轮一项只生一张，返回后receive，不额外测试或探测生图。普通制作不调用聊天列表或聊天读取接口，当前聊天也不回读；用户明确要求引用另一个聊天时只读取指定对象。恢复用status及requests_file，不从聊天找参数、编号或进度。上述访问规则约束执行行为，不是操作系统层面的读文件权限封锁。

| 当前动作 | 命令 | 模型只提供 | 脚本自动完成 |
|---|---|---|---|
| 开始 | `start --sources "<素材目录>"` | 素材目录 | 清点当前目录图片，编号、尺寸、哈希、快照、首次准备表 |
| 准备当前阶段 | `prepare --stage head --config "<输入.json>"` | 一次角色/素材分析及本阶段语义 | 固定模板编译、实际输入容量检查、验收目标、配方、请求参数 |
| 更新已保存选材 | `update-plan --stage back --config "<原选材输入.json>"` | 修正后的materials/accessory_visibility及可选character | 一次校验并保存，失效相关批准，返回最早阶段；不发生成请求 |
| 工具返回 | `receive --stage head --config "<结果.json>"` | 本批request_id、原始回执、可取得的真实tool_call_id（不可得填null） | 一次归档、调用次数、证据、拼图、记录 |
| 制作端简评 | `self-check --stage head --config "<简评.json>"` | 拼图id、简短实际观察、pass/fail | 绑定候选与版本；通过才开放代理交接 |
| 代理返回前预检 | `check-report --stage head --config "<代理报告.json>"` | 同一份实际报告 | 只读检查字段、适用证据和查看引用；不保存批准 |
| 子代理报告 | `review --stage head --config "<代理直接写的报告.json>"` | 代理实际观察/结论 | 字段预检、像素采样、固定记录、下一步；不重抄报告 |
| 首次待核实 | `supplement --stage head --config "<补证.json>"` | 疑点及实际裁切坐标 | 一次补证局部/对照、pending报告模板、原代理交接 |
| 三选一或上限选优 | `rank --stage head --config "<选择.json>"` | 拼图版本、所选编号、一句理由 | 排序记录、选定或回退、必要复核入口 |
| 正面最终选定 | `record-structure --stage front --config "<返回的空间分析.json>"` | 所选正面的附着、走向、遮挡与背/侧可见范围 | 固定清单、来源绑定、编译背/侧目标；不生图、不做合格评价 |
| 四阶段完成 | `deliver`，实际看图后再`deliver --config "<交付简查.json>"` | 两项简查观察 | 四PNG、全部候选数据、现成网页、总览、角色描述、清单、复制与最终核对 |
| 恢复 | `status --stage head` | 当前阶段 | 返回已经准备好的下一步；不生成、不重验、不写记录 |

阶段键固定`head/front/back/left`，依赖顺序固定特写→正面→背面→左侧面。接口内部完成网页的left→side映射。

最终位置在start绑定素材目录。只有用户明确另定位置，才同时加`--destination "<用户目录>" --destination-user-quote "<用户原话>"`；这两个参数仅start接受。没有用户另定要求就省略，不从工作目录推测交付目录。普通deliver不接收destination、destination_reason，不能因权限错误临时切换到outputs。

## prepare

首次提交就检查全部计划阶段的用途冲突，一次返回所有可检测路由问题。field_errors逐项给field/source_id/use_id/stage及correction；不等背面阶段再逐条失败。正面设计不能作背面设计答案，材料部分拆成material用途；仅移除错误的back路由，保留有效front/left路由，不为了过检查丢失侧面鞋型等设计依据。

开始只读选材规范，实际查看每张原图和确有必要的细节。观察与采用决定一次写入start生成的`制作记录/阶段输入/首次准备.json`。先分析内容，再决定采用；同一文件即可，不另写Markdown。脚本会自动生成图片选用文档和角色描述。

首次输入示例（文字须换成本任务真实观察；不填写哈希/证据标签/依赖）：

```json
{
  "character": {
    "name": "角色名称", "height_cm": 163, "age_years": 19,
    "appearance": "依据本任务观察写实际外貌",
    "outfit": "本套服饰及材料", "outfit_name": "描述性造型后缀",
    "setting_basis": "身高年龄沿用来源或本次估计依据"
  },
  "accessory_visibility": [],
  "materials": [
    {"source_id":"s001","views":["front"],"observation":"本图全图内容、可见身份与服饰的实际观察","quality":"分别记录脸、衣料、图案的清晰度、遮挡和污染","accessories":[],"decision":"adopt","selection_reason":"与其他图比较后保留的互补依据","uses":[{"id":"face_identity","kind":"identity","stages":["head"],"target":"本图可辨的实际脸型与五官关系"}]}
  ],
  "prompt": {
    "operation":"generate", "identity":"本角色实际身份特征",
    "references":[],
    "critical_constraints":[]
  }
}
```

`materials`必须覆盖全部来源，每项必有accessories（无饰品填[]）；所有单张观察完成后填写顶层accessory_visibility（无饰品填[]），固定结构与综合顺序见[饰品观察及汇总](material-selection.md#饰品观察及汇总)。后续阶段直接沿用这份汇总，不重写目标或提示词。不接受遗漏已识别饰品的汇总，初判possibly_hidden不能当最终状态，仍不清楚填unclear并写理由。`decision`固定`adopt/partial/exclude`；`views`为实际视角的不重复列表，固定`front/back/side/rear_oblique/detail/unclear`，局部看得出背面仍标back或rear_oblique，不能只写detail掩盖视角。`observation`记录图中实际内容；`quality`单独记录分区清晰度、模糊、遮挡和可用边界；全部观察完成后填`selection_reason`说明与其他素材的比较依据，再决定采用。不能只写“已看”。`uses`为固定对象列表，每项必须有全任务唯一英文`id`、`kind:identity/design/material`、`stages:[head/front/back/left]`、`target`，必要时加本张原图像素`crop:[x0,y0,x1,y1]`。采用项须有uses，exclude须为空；不接受自由字符串用途。同部位优先清晰且互补的图，不能按整图像素大小自动判清晰。完全重复且更模糊的排除图可选填covered_by:{"source_id":"s002","reason":"清晰图如何覆盖本图全部相关信息"}，脚本据此替换残留手写引用；含独有必需细节的图保留partial及对应uses，不能填covered_by。脚本不把视角标签或非空文字当作视觉正确证明。身高年龄按主文档沿用/估计并告知，setting_basis不进入角色描述正文。

选材计划是唯一语义来源：例如袜料写`{"id":"stocking_surface","kind":"material","stages":["front","back","left"],"target":"实际可辨的透明度、织物和图案尺度","crop":[10,20,300,400]}`（坐标须换成本图实际值）。对应prepare自动加入原生局部、提示词和planned_复核项；同类别最多三块合成一个比例适中的辅助输入，保留来源、裁切、原生与显示尺寸、真实缩放及分区旁录；位置说明自动进入提示词。没有新修复时不再复制这段目标到后续critical_constraints，也不另写选材表。阶段references只写必要上游和计划以外的证据；已列入uses的原图/局部自动处理，不手动重复占槽。接口返回与记录保留reference_coverage，可核对各特征实际进入哪个输入。planned_为保留前缀。拼合后目标仍绑定各自原始证据；实际输入编号按reference_index读取，reference_region为拼图内分区，reference_key保留原始目标证据键，不能把整张拼图当作所有检查的共同答案。

背面prepare自动同时加入正面原图（front_material，仅材料纹理与表面光影）和镜像剪影（back_silhouette，低优先级身体比例辅助，不作服装轮廓蒙版）。back/rear_oblique原图决定后方设计、裙摆、拖尾及脚部遮挡，优先于剪影；不照搬正面下缘、开口或露脚位置。未引用后方原图时自动补一张back优先依据。普通stage:front保留为材质原图，另加剪影；直接传正面素材或同文件别名也强制限定材质角色，不能用role误称后方设计。手写panels不能把正面材质与后方设计混成一个未区分用途的参考；自动容量拼图可以同图分区，并逐区保留不同guide和目标来源。front_material可由source_id/file/panels提供，但不能标为rear_design以绕过限制。正面缺少可靠Alpha时报告无法可靠提取剪影，按既有背景规范准备并核验分割；不能按白色RGB删除背景，也不现场研究源码。前后细节无需相同；全身完整入画不要求把被长裙遮住的鞋露出来。规则自动进入固定提示词、对照和复核目标；rear_reference是接口保留id，角色约束另取id。

后续阶段只写`prompt`及确需的本阶段配置，不重复角色/素材分析。参考选择一种：`source_id`为已采用原图；`stage`为有效上游；`file`为已有辅助图；`panels`可自动整理同主题原生裁切，一项参考占一个工具槽位。每项必须写限定用途`role`，辅助图可写固定`guide`。例如正面输入`{"stage":"head","role":"身份基准"}`。图像工具默认最多5项参考，能力确有不同才填max_reference_images。prepare先应用已记录的清晰替代关系、合并同文件用途，再在超限时自动拼合，直到满足数量；不丢掉任何保留的必要原图或局部。优先拼正面材质原图+剪影，再考虑同用途证据；若仍超限继续合并。搜索横排/竖排/多行布局，优先紧凑、少空白；上下图等宽，同排图等高，保持各图比例（允许整数像素舍入）。整图9/16≤宽/高≤4/3；只有超限才补最少空白，不强制正方形。较小图等比例放大对齐，不旋转、不缩小关键纹理，原始素材保留。唯一编辑底图始终独立；容量连底图及一张必要参考都装不下时明确报错。接口返回reference_packing与reference_coverage，不需要另外status或看源码。

所有通用构图、光照、表情、眼部呈现、背景、质量、编辑保留语句均自动插入。默认自然睁眼，原图闭眼写在observation中，不写成identity/uses.target的保持闭眼或不推断瞳孔要求；没有清楚睁眼依据时按当前角色风格自动补全瞳孔，后续沿用所选特写。模型不得写自由通用段落。角色变量详细字段只在需要时查[提示词字段](prompt-construction.md)，不读生成器源码。`critical_constraints`同时生成验收设计目标，identity同时生成身份目标，用户覆盖项同时生成呈现目标；不再另写同义验收清单。

planned_和visibility_为脚本生成目标的保留前缀，手写critical_constraints.id另取编号。确有新证据修改饰品汇总时，先对受影响的最早已准备阶段prepare；受影响下游目标失效，无关阶段不重验。不能在准备背面时顺带修改特写已采用的可见性结论。

可选顶层字段：`reviewer_policy`、`generation_limit`（默认6）、`required_calls`（默认3，例外须`user_override_reason`）、`strategy_change`、`review_regions`、`comparisons`、`max_reference_images`、实际`tool/parameters`及其他工具所需的`argument_fields`。reviewer_policy为`{"mode":"prefer_subagent","model":null}`；用户指定模型填实际名称；禁用填`{"mode":"self","model":null,"user_override_reason":"用户原话"}`。

来源局部不另写裁图脚本，直接在同一prepare的references中使用：

```json
{"role":"本轮服装材料和肩部位置依据","panels":[{"source_id":"s001","crop":[0,300,500,700]},{"source_id":"s002","crop":[100,200,550,650]}]}
```

panels是一至三个已采用原图的同主题局部，坐标来自实际图片。脚本等比例对齐排版并保存来源/裁切、native_size/display_size及actual_scale_xy旁录；不自动缩小关键细节；每个参考项仍要保留身份与连接上下文。

只有本轮已出现明确歧义、比较尺度需要或小细节在默认拼图看不清时提供裁切：

```json
{
  "review_regions":[{"id":"shoulder","crop":[0,500,700,1000]}],
  "comparisons":[{"reference_indices":[1],"candidate_crop":[0,500,700,1000],"check_ids":["design_shoulder"]}]
}
```

坐标必须来自实际图片；示例不是通用坐标。默认展开脚本已知的生图组合参考为原图/原生局部，按来源去重后分组；每张只放一至两张独立参考加一张候选。同一参考默认只展示一次，各图check_ids关联其覆盖目标；不再同时生成“全部参考”及子组，不把全部素材两两拼完。自定义comparisons只用于确有必要的不同局部或尺度。服装比较肩、颈根、上臂位置和露肤范围；合理小偏移接受，明显改穿法不能按同款同色通过。

其他生图工具示例：`{"tool":"实际工具名","argument_fields":{"prompt":"工具的提示词字段","references":"工具的有序本地参考字段"},"parameters":{"工具其他参数":"实际值"}}`。字段名和参数从该工具实际接口填写一次，脚本冻结并直接返回完整arguments；不替其他工具注入image_gen的透明参数。不可用或不支持本地参考的工具报告具体限制，不猜字段。

输入格式错误返回correct_input和具体字段；无额外critical_constraints填空列表，不能填自由字符串。request_file_access表示文件权限不足，按返回目标申请访问并原样重试当前命令，不当成输入/视觉失败，不改记录或重生图。

## 当前阶段才继续阅读

生成工具返回后，按接口返回动作读[生成结果、复核和排序接口](production-review-api.md)的相应段落。正面选优结束且收到analyze_front_structure时才读下一节。四阶段选定后才读[交付接口](production-delivery-api.md)。制作记录由脚本生成，普通制作不研究记录Schema、打包或网页实现。

## record-structure

只在所选正面最终确定（批准或上限选优）后调用，由制作模型完成。rank或正面优胜候选的最终review返回analyze_front_structure、selected_front、rear_sources、config_file和submit_command。直接填返回文件，不另写分析表、提示词或裁图脚本。复用刚看过的排序图和原图；后方关系尚未看清时只看返回rear_sources中的必要原图，不再完整验收，也不唤起代理。

模板只有binding_sha256和items；binding保留返回值。items清单及id/name固定：hair_flow、clothing_layers、全部accessory_<饰品id>及选材计划中全部feature_<设计用途id>，必须逐项覆盖。hair_flow写清各重要发束/辫子的实际根部、跨肩路径和末端所在面；clothing_layers写清穿戴层次及前后归属，不能仅写“共享结构”。其他行分别核对对应饰品和服装特征。

每项固定字段如下，示例仅说明结构，文本替换为实际观察；id/name沿用本次模板：

```json
{
  "id":"hair_flow", "name":"头发、发辫走向与前后遮挡",
  "front_observation":"所选正面可见的实际表现",
  "attachment":"实际根部或连接对象", "path":"从固定点跨过哪里，末端在哪一面",
  "occluded_by":"实际遮挡物；没有时写无", "source_ids":["front_selected"],
  "stages":{
    "back":{"visibility":"visible","visible_portion":"后脑根部及跨肩上段","hidden_portion":"垂在胸前的下段和辫端束饰","reason":"后方原图与正面跨肩位置的实际依据","source_ids":["front_selected","s004"]},
    "left":{"visibility":"unclear","visible_portion":"有依据的近侧部分","hidden_portion":"未显示或被遮范围","reason":"仍不确定的具体范围","source_ids":["front_selected"]}
  }
}
```

visibility固定visible/not_visible/unclear：部分可见用visible，同时分别写可见与隐藏部分；全部不显示用not_visible，不确定保留unclear。各文字字段非空；source_ids不重复，每项整体必须引用front_selected，阶段分别引用实际采用的素材编号或front_selected。有后方原图时，确定的背面判断必须引用实际后方依据；不能拿同一实体或正面图证明背面完整可见。没有后方证据可作有依据的合理方案，reason注明推断范围。清楚后方原图高于所选正面的误生成。

脚本一次保存固定JSON、来源哈希并编译本阶段可见/隐藏说明；不抄初始汇总错误，不把胸前辫端或束饰移动到肩后。成功返回prepare_next_stage及背面prepare配置。未知/缺失字段、漏项、来源错误或旧binding在保存前拒绝，修当前返回模板即可；不生图。补文字不自动批准图片。分析引用的所选正面、上游或有效设计计划变化时分析失效；修改空间方案使已准备的背/侧批准失效，并返回重新prepare入口，累计调用次数保留。仅修改质量说明等未改变事实的记录不失效空间方案。

## update-plan

仅已保存计划需要修正时调用。直接编辑原选材输入文件（通常为首次准备.json），一次提交全部materials和accessory_visibility，可带character。原prepare文件的prompt及其他已支持阶段字段允许保留，但本命令只应用character/materials/accessory_visibility，不应用阶段提示词、工具参数、代理策略或调用上限；未知字段仍报错。

`update-plan --stage back --config "<原选材输入.json>"`中的stage是准备恢复的阶段。脚本校验成功才保存；失败不修改已保存计划、批准或调用次数。存在尚未receive的请求时先登记该批结果，不能在工具执行期间改变目标。

返回plan_saved、affected_prepared_stages（依特写→正面→背面→左侧面排序）及prepare_config_files。已准备阶段受影响时，返回最早阶段的config_file和submit_command；直接执行，不重新拼写阶段输入。无已准备阶段受影响时，原样重试刚才的阶段prepare。更新本身不发生成请求，既有调用次数及失败累计保留；相关批准立即失效，重新prepare和实际验收前不能进入下游。只改观察措辞、排除说明等未改变生成事实的记录，不失效图片批准。

在prepare仍使用旧计划时，错误返回plan_config_file和plan_submit_command，明确先更新哪个文件。不要反复改back-prepare、重新跑head或修改生产接口.json；这些都不能代替显式更新共享计划。

## 每一步返回字段

| next_action | 直接使用 | 填写范围 | 提交后 |
|---|---|---|---|
| generate / generate_parallel | requests、requests_file、receipt_config_file、submit_command | 原样调用arguments；每项当前回执填result/error，编号可得才填 | 整批receive |
| self-check | comparisons、config_file、submit_command | result、observation、viewed_evidence_ids；fail另填issue_key | 通过才返回代理交接 |
| review_first / review_selected / review_processed | reviewer_handoff | 按dispatch_mode调度本阶段代理 | 代理完成查看、写报告、check-report后一次返回 |
| rank | ranking_board、config_file、submit_command | preferred_call、reason | 首张复用批准；新增胜出才复核 |
| analyze_front_structure | selected_front、rear_sources、config_file、submit_command | 固定清单中的实际空间关系 | record-structure后进入背面 |
| prepare_next_stage | next_stage、可选config_file/submit_command | 有配置时直接使用；正常新阶段只填当前语义 | prepare |
| inspect_delivery | images、inspection_config_file、submit_command | 两项实际简查 | deliver |
| done | destination、preview_file、editor_file | 无 | 展示入口后停止 |

路径均使用返回值。pending模板从不自动填写已看或通过；已填写的同版本模板不覆盖。status只读，不修改任何模板或预留编号。requests_file持久保存本批真实工具名、参数和request_id，不是已调用证明；仅在恢复且确实丢失参数时读取一次。每项调用返回后立即保存request_id与结果对应，避免与上阶段输出混淆；不能用上一张图片的编号/路径登记当前请求。

## 制作记录格式


解析入口固定`制作记录/制作记录.json`，规范见[记录契约](production-record-format.md)和[JSON Schema](production-record.schema.json)。该JSON及三份Markdown均由脚本生成；模型不要重新撰写、移动字段或补造历史。
