# 制作记录契约

解析入口固定为`制作记录/制作记录.json`。UTF-8 JSON，`schema_version:3`，`record_type:"character_reference_production"`。仅维护当前契约，不支持旧版本兼容或猜字段。正式结构见[JSON Schema](production-record.schema.json)。Markdown只用于阅读，禁止作为独立解析或批准来源。

| 字段 | 固定语义 |
|---|---|
| created_at | 脚本读取的真实UTC ISO 8601开始时间 |
| character | 固定名称、身高、年龄、外貌、服饰材料、造型后缀和数值依据；未提交为null |
| source_directory | 本次原始素材目录绝对路径 |
| sources | 全部原始图片，稳定source_id、原路径、根目录相对快照路径、SHA-256、宽高与模式 |
| material_analysis | 按source_id关联的views、observation内容、quality分区质量、decision采用决定、selection_reason比较依据及结构化uses；uses固定id/kind/stages/target，可加原生crop；views固定front/back/side/rear_oblique/detail/unclear的不重复列表，不自动写“已查看” |
| stages | 按head/front/back/left顺序列出已准备阶段；未开始阶段不出现 |
| recipes | 按配方指纹索引全部历史及当前配方；每次调用的recipe_sha256都可直接关联 |
| requests | 稳定request_id、stage、配方指纹、工具、awaiting_result/recorded、实际工具调用id及call_id；未返回字段为null |
| front_structure | 正面最终选定后固定空间分析文件引用；未提交为null；明确为制作观察而非视觉批准 |
| delivery_override | start时用户明确另定的destination/user_quote对象；普通任务固定null，deliver不得临时修改 |
| delivery | 尚未整理为null；之后固定交付状态、绑定、预览、关系拼图、实际简查、目的地及三类状态 |
| record_semantics | 明示视觉结果来自模型实际观察，脚本不做视觉判断 |

每阶段固定保存decision、目标指纹、依赖、targets、target_sources、recipe、提示词编译元数据引用、调用上限、批准首张、当前所选、实际复核策略、calls、ranking、ranking_evidence、supplement（补证批次引用或null）、limit_selection、processing及processing_reviews。

每个真实调用固定保存call_id、phase、全局event顺序、配方指纹、receipt、review_packet、output、error、self_check、visual_review、review_decision。review_packet引用本次冻结的目标/依赖/证据包；没有任务包时为null。工具失败output为null，成功时error为null，二者互斥；没有审查的候选self_check/visual_review/review_decision均为null。工具失败仍占一次调用。不得用空报告伪装已验收。

文件引用固定`{"file":"根目录相对路径","sha256":"64位小写十六进制"}`。带data的报告引用额外保存冻结报告正文；data中的checks保留具体参考事实、候选表现、比较依据、证据id和pass/fail/pending。报告路径与哈希用于检查正文一致性。原始来源original_file和最终destination是绝对路径；文件移动后其余项目引用仍按制作根目录解析。特殊的原始实际工具路径仅出现在真实回执，不用于寻找已归档候选。

recipe保存prompt_file/prompt_sha256、有序inputs、tool及parameters。解析提示词正文时按prompt_file读取并核对哈希。requests只引用配方指纹，不再内嵌整段提示词。阶段输入是模型一次提交的语义原件；目标、提示词和Markdown从它派生，不要求模型重抄。

recipe.inputs各项固定保留file/sha256/purpose，可选guide或regions按实际输入保存。复核包generation_context由脚本生成：真实生图时status=recorded，固定含recipe_sha256、prompt_file、prompt_sha256、prompt全文、tool、parameters、references；references按实际顺序编号index，其余字段与recipe.inputs相同。既有图审查或后处理无新的登记配方时status=not_recorded并记录reason，不猜测历史提示词。提示词全文只自动进入复核包，模型不重复填写制作文档；上下文绑定实际调用配方，文本、用途、顺序或文件变化使核对失效。design目标的evaluation_scope由固定模板生成，限定当前角度可见的比较范围，不预填视觉结论。

decision只表示当前记录：unreviewed、rejected、approved、selected_unreviewed。null表示记录不存在，不等于pass；pending表示已有观察仍待核实。stages.plan_requires_prepare为true时表示共享计划已修改、尚未重新编译本阶段目标，旧批准及依赖立即失效；target_sha256仍保留当时真实目标哈希，不制造替代哈希。重新prepare后此标记清除，但不自动恢复视觉通过。visual_assessment的recorded_only表示有模型判断记录，绝不表示脚本证实图片合格。ready_to_copy尚未完成交付；只有最终目录核对成功后才记录complete。

三份Markdown使用`assets/record-templates/`的固定模板：图片选用文档、角色描述、制作记录摘要。标题、列名、字段顺序固定；观察中的换行和竖线由脚本转义。角色描述正文只含名称、身高、年龄、外貌及本套服饰材质，数值推断依据留在JSON。

低层验收目标、状态、三选一、流程状态、任务包和报告保留供证据/版本门禁追溯；新解析程序优先消费本聚合JSON，不扫描临时文件名猜测流程。表格补齐、重新导出或文件完整性通过均不能自动恢复失效视觉结论。

targets中的source_ids按同阶段target_sources解析；该映射包含已冻结原图、上游或辅助图的文件引用。历史调用按review_packet读取当时目标/依赖，按recipes解析当时配方，不能套用当前目标。

背面target_sources的upstream_front_silhouette为原生镜像剪影；upstream_front_material为正面原图，仅约束材料纹理与表面光影，不能决定后方设计；依赖仍绑定当前front阶段版本。剪影旁录保存原图与输出哈希、Alpha阈值及镜像变换，后方设计检查独立引用背面/侧后原图。stages.reference_coverage由脚本生成，记录特征id/kind/target、source_id/crop、实际工具reference_index及可选reference_region，与自动planned_目标对应；reference_key是目标原始证据键，拼图时不等于整张工具输入文件。可选material_analysis.covered_by记录清晰替代来源与覆盖依据，只用于无独有必需细节的exclude项。stages.reference_packing固定记录capacity、before_count/after_count、source_replacements、duplicates_removed及collages；每张collage含实际reference_index、file/sha256、record_file/record_sha256，regions逐项保存id/location/box/role/source_key/guide。拼图旁录保存每区的原图哈希、裁切、真实actual_scale_xy及画布像素位置；不能把等比例放大的拼图区写成倍率1。所有这些字段由脚本生成，不要求模型重抄。delivery.layout保存实际排版，提交简查时自动复用；配置文件位置不参与视觉版本绑定。复核包的native证据保留region、crop、scale:1、subject_bounds和定位方法；这些是裁切依据，不是视觉识别或合格结论。背景原报告允许保留confirmed_empty:false，规范化检查只使用实际确认的true点；不自动确认空白。

参考拼图旁录固定使用artifact=aligned_bounded_reference：output_size为实际整图尺寸，content_size为补边前内容尺寸，aspect_bounds固定portrait:[9,16]、landscape:[4,3]，resampling为LANCZOS，resized如实记录。每区保留来源与crop，并记录native_size、display_size、actual_scale_xy和真实box。原生素材与复核证据不被缩放图覆盖；缩放或补边不代表视觉通过。

实际调用receipt文件固定保存request_id、actual_tool_call_id、tool_call_id_status、recipe_sha256、received_at、output/error、raw_receipt、original_output、provenance。original_output在成功时含file、sha256、format、dimensions、conversion和archive_file；失败时为空。tool_call_id_status固定recorded/unavailable_in_current_receipt；后者actual_tool_call_id为null，表示当前工具未提供编号，不表示工具未调用或验收通过。编号从不补造，receipt为模型提交的工具回执，脚本没有独立验证平台调用。非PNG工作副本仅保存解码后的原尺寸像素，原始输出及哈希仍保留。其他工具recipe.argument_fields保存真实提示词/有序参考参数名，参与配方版本绑定；不推断调用id或视觉通过。

material_analysis[].accessories保存逐图饰品观察与初判；顶层accessory_visibility保存全部单张分析完成后的四阶段结论。两者按饰品id关联；最终visibility固定visible/not_visible/unclear/out_of_frame，只有逐图other_views允许possibly_hidden。name/carrier/location沿用实际实体和所属面，stages各项有source_ids/reason，必要时location定位当前可见区域。脚本只验证字段与来源、编译提示词，不自动推断可见性；不可见目标仍纳入本阶段复核。

front_structure引用文件按[空间分析Schema](front-structure.schema.json)解析：artifact固定front_structure_analysis；binding_sha256绑定所选正面sha256/依赖、背/侧有效设计计划和固定清单；front_sha256绑定实际所选图；sources按front_selected及素材source_id保存file/sha256；items为固定清单，id/name/front_observation/attachment/path/occluded_by/source_ids/stages不增删字段。stages仅back/left，字段visibility/visible_portion/hidden_portion/reason/source_ids固定。semantics固定maker_observed_relations; not_visual_approval。新清单默认空观察和unclear，脚本不自动填写可见性。来源或方案改变使相关已准备背/侧批准失效；补记录不恢复通过。

targets.reference_display可为compare/context_only；context_only用于当前画外特征的误添加检查，来源仍绑定在target_sources，但不自动打开异视角原图或放入复核拼图。复核包comparison证据的source_ids/check_ids/reference_scopes分别注明原始参考键、覆盖检查项和每张参考的target/evaluation_scope；它们限定参考用途，不能据一张无关参考声称核对另一项。生图压缩拼图按原始用途展开，planned_目标只引用对应原图/局部，避免其他分区串成答案。

check-report只读预检不创建记录或批准；报告中的viewed_evidence_ids只能来自实际查看。通过项须有适用主要证据，identity/design/spatial还须覆盖对应原图；native_quality/reduced_quality不能借comparison代替。阶段复核agent_id禁止跨阶段复用，返修/补证/选中新增图只复用本阶段代理；实际模式仍按reviewer记录。脚本验证字段与引用，不能证明真实观看或理解。

delivery.destination从预览时固定，默认素材目录新子目录，重试保持不变。复制在外层同父目录保存本次.sheet-copy.json中断标记，按包/目标/文件哈希续传；它不是制作记录或视觉批准来源，成功后移除。最终交付元数据在复制前派生，制作区complete只在最终目标核对成功后写入。delivery_override只能追溯start时的用户要求，不能把模型临时改路径写成用户原话。
