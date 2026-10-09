# 生成结果、复核与排序接口

只到当前动作才读相应段落。入口与公共参数见[制作接口](production-api.md)。

## 调用和receive

**成功返回与预览显示分开。**工具已经返回成功图像时，即使显示助手报错、没有预览、回执解析或receive失败，也只修显示/登记，不再生一张。调用时按request_id保留完整原结果；functions.exec可用store保存回执供后续load，不用JSON.stringify(result).slice(...)截断唯一保存路径。短工具输出应完整保留原始文本回执、output_hint或工具提供的实际路径字段，图像数据按工具规定显示，不能把base64正文打印到聊天。不要从本地文件重造一个image_url假装原工具回执。工具确实失败才填写实际error；重试仍受当前请求及阶段累计额度约束。

prepare返回`tool_call_count`和`requests`，数量一致，每项包含稳定request_id和可直接传给工具的arguments。首轮只有一项，只调用一次；成功返回后直接receive。不调用测试、探测、占位或无关提示词，不把下一轮追加提前到首轮。**原样调用arguments**，不另写提示词、不加已有候选、不调整输入顺序。parameters禁止覆盖prompt或参考，透明参数必须与背景分支一致。请求是已准备的参数，不是工具已执行证明；中断恢复先核对已有工具返回，有结果直接receive，不能重发。平台工具日志保存实际请求；接口回执记录模型提交的真实调用id，可据此追溯，不声称脚本独立证实工具调用。

首张复核通过后直接返回两项requests。同一次exec用`Promise.allSettled`并行调用，全部结果到齐后一次receive。两项调用之间不看图、不登记单张、不检测透明度、不评价；回执只登记工具成功/失败，脚本检查文件解码和完整性不属于视觉审查。

```json
{"calls":[
  {"request_id":"head-0002","tool_call_id":"真实调用id2","result":{"savedPath":"工具实际返回的本地图片路径"}},
  {"request_id":"head-0003","tool_call_id":null,"error":"工具实际失败原因（本回执未提供调用编号）"}
]}
```

每项调用在当前上下文就保存request_id与原始返回的对应，全部到齐后直接编辑返回的receipt_config_file，再执行submit_command，不另造结果文档或正则解析器。成功填写原始result（结构化对象、content、output_hint或文本回执均可）。output_hint含保存路径时直接提交该原始回执，不把本地图片转成base64、重建伪tool_result或自己解析路径；图片字节由脚本归档。有真实编号时也可只提供output；原始result包含多个本地输出时，可同时填output选择其中一张，脚本保留完整result并核对选择确实在原回执中，绝不猜选或允许换成别的图。失败才填error，去掉result/output；原始结构化result标记isError:true时如实登记失败，不能另填output强行变成成功。tool_call_id只填当前调用上下文或原始回执提供的真实编号；未提供则保留null，记录tool_call_id_status:unavailable_in_current_receipt。有编号记录recorded；显式编号与回执编号冲突时拒绝。不为填编号读取聊天，不从图片名提取，不要求exec前缀或某种目录命名。无编号时必须提供原始result或真实error，不能只有output路径；脚本保留request_id、回执和文件哈希以追溯，但不声称独立证明调用。支持可解码的单帧PNG/JPEG/WebP/BMP/TIFF等静态格式，保留原始字节及哈希；非PNG按解码后的原尺寸RGBA保存PNG工作副本，不缩放、去噪或补透明，不称原生PNG。raw_receipt与original_output保存在实际调用回执，不重复写入语义文档。

请求编号以接口返回为准。失败没有output，不抹除失败、不凑三张成功、不因效果差再抽。脚本拒绝只登记本批一张；全部登记后自动生成可用候选拼图。上限不足两次时只发剩余额度，到墙由制作端选优，保留未批准状态。

## self-check和review

receive已一次生成代理所需对照、按部位的原生局部、缩小图、深浅底拼图和pending模板。制作模型直接填返回config_file（已固定call_id和review_token）并执行submit_command，不另建简评文件或查找模板；只开返回的参考—候选拼图，一两句简查明显取景、身份造型、头发面部、服装位置、双臂下垂及饰品承载物/可见面问题，不展开原生/缩小/背景全流程，也不重复生成代理证据：

```json
{"call_id":"接口返回编号","review_token":"接口返回值","result":"pass","observation":"实际看到的简短表现","viewed_evidence_ids":["compare_001"]}
```

fail须加稳定`issue_key`，自己修后重新prepare；不能交代理。通过后立即按reviewer_handoff调度，不等待其他无关准备。代理完成全部适用查看、写report_output_file，执行check_report_command预检后一次返回最终路径和结论；主模型直接review该文件，不把JSON从聊天抄回文件。

交接固定字段：stage、dispatch_mode、agent_id、model、task_name、root、packet_file、packet_sha256、report_template_file、report_output_file、check_report_command、task。普通制作返回的root和report_output_file为绝对路径；packet_file/report_template_file为root相对路径，按root解析。task已经包含解析后的本次路径，不猜assets/evidence等不存在的嵌套字段。model=inherited表示同主模型，用户指定的模型按原值使用。dispatch_mode只按返回值执行：

| dispatch_mode | 直接操作 |
|---|---|
| spawn_new_stage_agent | 为当前stage新建独立代理，fork_turns:none，只传本次task及交接字段；不继承制作聊天或其他阶段结论 |
| reuse_stage_agent | 只联系返回agent_id，供本阶段返修、补证或所选新增图；不得换用其他阶段代理 |
| full_self_review | 按真实原因完整自评；不启动代理，仍须完成相关细节、缩小和背景查看 |

每阶段首次各自新建（head/front/back/left互不复用），跨阶段使用过的agent_id会在预检及登记时拒绝；本阶段代理确实不可用才填reviewer_replacement_reason并新建本阶段替代代理。子代理不联系其他复核代理，不先向父代理发中途通过/进度或催确认。主模型不把其他阶段观察交给它当答案，也不要求它尚未看完就汇报。

## check-report

由当前复核者写完report_output_file后，原样执行返回的check_report_command；与review接收同一visual格式，不另建检查表。命令只读，不修改模板、候选、批准、调用次数或制作记录。返回report_ready且visual_approval_recorded:false仅表示字段/引用就绪；然后一次向主模型返回最终报告路径和结论，主模型执行review实际登记。

错误返回correct_report_fields及field_errors：缺主要证据、引用未查看id、漏检查项、未知字段、旧版本或代理跨阶段复用等。不把拼图引用充当native/reduced验收，也不引用不含本项适用原图的其他拼图。比较时按每张comparison的reference_scopes核对对应目标；reference_display=context_only只检查候选是否误加画外特征，其异视角来源不再次打开。确实漏看则在本次交接内补看对应证据再写实际观察；不是改个已查看字段就能通过。格式错误按列出的字段一次修完；视觉pending/fail如实保留。预检检查记录一致性，无法证明模型真的查看或理解了图片。不得先交父代理“全部通过”，再请求父代理发现漏项或补写已看列表。

任务包的generation_context自动保存本次真实提示词全文、配方哈希和有序输入用途（含guide、拼图regions）；主模型不另抄提示词，也不要求代理找提示词文件。交接task明确本次packet_file、report_template_file、report_output_file绝对路径；代理各读取一次，不枚举JSON字段、查询目录或重复提取同一提示词。代理先据此确认每张图/分区参考什么，再按evaluation_scope和当前视角看图。侧面头部辅助图能补头饰/发型，正面特写不展示侧脸不是失败；自然不可见部分说明范围并查可见矛盾，无矛盾可通过该项，不能冒称隐藏细节已证实。默认自然睁眼；原图闭眼不构成身份锁定，缺少可辨瞳孔依据时接受与当前画风、配色相容的合理补全，不以原始瞳色无法核验判pending/fail。后续沿用所选特写，刘海自然遮眼及背面不可见允许；用户明确要求优先。提示词不是视觉答案；生成留白不能成为额外门槛，头顶/头饰仅触边可通过。后方裙摆、拖尾及遮脚关系优先于正面剪影，长裙盖脚不要求露鞋底。

完整复核直接用包内已生成文件，遵循review_sequence；不自行裁图、拼图或生成背景。先看相关拼图，每张最多两张原始参考/原生局部加候选，不嵌套生图参考拼图；同一来源默认只出现一次，不另看总拼图。已看清的不再打开原文件；原生质量按region选择头发、面部、胸腰、裙摆、腿或鞋等有关局部，坐标保留原生尺度，空白块已过滤。缩小质量与原生质量各承担自己的判断。背景只看alpha_compare，不再看light/dark；图中1、2标号对应background_sample_candidates顺序，实际空白填confirmed_empty:true，非空或不确定保留false即可。脚本只使用明确确认的点，无需删点、量坐标或检查像素。至少确认一处才能使背景项通过。模板预置预期复核模式，子代理填写自己的实际agent_id，完整自评须按真实模式及原因改写；不能发明independent_visual_review等枚举。

每项写实际参考事实、候选表现、位置/证据id及pass/fail/pending；头发与面部分开观察。背面先对照背面/侧后原图的实际可见设计；“与正面共享结构”不能证明背面裙层、后摆和装饰正确。制作目标是待核验的方案，不能当原图事实；目标误写或与原图冲突时按原图指出差异。合理的头发/饰带遮挡只查可见边界、连接及穿戴，无矛盾可通过，说明隐藏细节未核实即可；不为看穿头发反复裁同一区域，不把不可见的普通后片接缝当作阻塞条件。关键可见关系看不清或冲突才补一次适用证据。饰品观察须写实际承载物、物件正背面、连接处及当前可见/遮挡范围；清楚原图没有装饰的可见区域不能无依据添加。返修沿用本阶段原代理，第二次起合理饰品位置/长度按如此设计接受，承载物或正背面归属错误、复制饰品以及与清楚原图矛盾仍拒绝，明显不合理及明显画质问题仍指出。

报告字段错误只按返回field_errors修字段，不重生图、不重读规范/源码、不擅自改视觉结论。用户禁止/工具不支持/启动失败则如实用完整自评，记录实际原因。完整自评与制作端拼图简评是不同记录。

## supplement

只在返回supplement_evidence时使用一次。由疑点决定裁切，不现场写裁图脚本；evidence_id默认whole，也可选原包source/upstream或已有native局部，坐标以该证据自身为准。需要对照时指定原包参考id及两边裁切：

```json
{"reason":"本轮实际待核实细节",
 "regions":[{"id":"shoulder","evidence_id":"whole","crop":[0,500,700,1000]}],
 "comparisons":[{"id":"wearing","reference_ids":["source_s001"],"reference_crops":{"source_s001":[0,300,500,700]},"candidate_crop":[0,500,700,1000],"check_ids":["design_shoulder"]}]}
```

只填实际必要项，regions/comparisons可省一项。脚本返回补证包、报告模板和本阶段原代理交接；不改原候选/原任务包。新模板默认pending/未查看，extra_evidence的标签/来源/用途由脚本冻结，代理不能改写。补证对照保留适用原图来源，可直接引用它判断该项，不为满足引用要求重复打开旧对照。代理确认完整当前观察集合、写report_output_file并执行本次check_report_command后一次返回，制作端review该文件。仍不确定保持pending；优胜追加图回退首张，首张等待用户或证据，不无依据重生成。重复同批输入复用文件，不生成第二批补证。

## rank

只看脚本返回的三张候选同屏拼图，直接填返回config_file中的preferred_call/reason，保留已填board_sha256，再执行submit_command。不要查旧排序文件、搜索哈希或另建文档。固定字段如下：

```json
{"preferred_call":"真实候选编号","reason":"一句具体取舍依据","board_sha256":"接口返回值"}
```

脚本自动绑定全部可用候选，不另写排序表。接近时保留批准首张。新增明显胜出时仅它再做拼图简评和完整复核；失败/补证后仍不确定回退有效首张，不补抽。落选图保持未验收。上限选优也用rank，制作模型选，不交代理、不改造成合格。正面最终选定后返回analyze_front_structure，按[record-structure接口](production-api.md#record-structure)先填写实际前后遮挡，随后才prepare背面；不是再验收一轮。


复核design_visibility_<id>时同时核对汇总采用方案、原图可见区域和当前候选。not_visible不要求显示隐藏的饰品，而是检查规定位置是否被错误新增；不因为正面参考或上游含该饰品就接受背面新增。与清楚原图相矛盾的承载面/复制错误不会被第二轮的合理细节宽容免除。
