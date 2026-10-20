---
status: accepted
delivered:
depends: []
---

# 冷啟動 onboarding：自選媒體 feed，投幾票就開始第一期

這份里程碑是 2026-10-18 經兩層設計討論定案的。起點是第一位試用者的回饋：一開始拿到的是我們預設的 feed，大多是沒興趣的內容，要投好幾輪票才調得過來。查那套試用部署的 D1 也看到，試用期間到 2026-10-18 只投了 1 票。`value-by-clicks-and-rank.md` 的「冷啟動有兩個輸入」寫的是同一件事，這份把其中「抓一部分文章給讀者 triage」那一半定成具體做法；由來源提出領域的另一半，由 `digest-by-topic-and-area.md` 負責。

## 這個里程碑定下什麼

- **新部署不帶預設 feed。** 試用精靈第 13 步替試用者複製預設來源的做法，以及 `sources.example.yaml` 這類範本，都要拿掉。
- **onboarding 是一個逐步精靈，放在獨立頁面 `/onboarding`。** `sources` 是空表時，/settings 會轉到這一頁。精靈依序走六步：輸出語言與時區、出刊時間、挑來源、投票卡片、通知、第一期。挑來源時，讀者從清單挑；系統立刻抓回這些來源的近期文章，讀者再對其中幾篇按「有興趣」或「沒興趣」。
- **精靈不放 Model、Pipeline、Areas、Tracking。** 前兩區是部署者的事，後兩區要先有幾期資料才有意義。vote similarity 由精靈直接打開，不問讀者。
- **清單由專案自己收集，只收媒體自己提供的 feed。** 清單以 A 級資料檔放在 repo 的 `src/cyris/` 底下，不收 RSS hub，也不收聚合 feed。
- **卡片有三種狀態。** 「有興趣」和「沒興趣」才算票，沒碰過的卡不代表任何意思。按過的文章以 accepted 或 rejected 加上 `triaged_at` 寫進 store，成為第一期的 seed。
- **文章經 run 原本的抓取路徑進來。** 有 rss Worker 的部署走 `POST /poll` 和 `GET /articles`，沒有的走 `RssSource`。同一個解析器，才能保證投過票的 URL 和第一次 run 看到的一致。
- **`sources` 是空表時，視為「等待 onboarding」，不是錯誤。**
- **清單的 tags 照 owner 的做法標。** 通訊社和即時新聞標 `news`，分析和科技媒體不標。
- **投票的偏好排序延伸到新聞分群。** 分群在截斷前，依它最貼近投票的成員排序。這項改動對所有部署生效，包括正式站。

## 已經具體到可以往下做的部分

**清單**
- 條目是 `SourceConfig` 的形狀（`src/cyris/domain/models.py:22-36`），可以直接走現有的 `POST /api/sources`。新來源的 tier 用預設的 `filter`（`:28`）。
- 映像只出貨 `src/`（`Dockerfile:33`、`docs/architecture.md:534`），所以清單放在那裡。`docs/architecture.md` §5 要加一列 A 級。
- 清單只在 onboarding 時讀一次，之後每套部署的 feed 都存在自己 D1 的 `sources`。area 不能由清單指定，因為 `area_id` 指向每位讀者自己的 `areas` 列（`models.py:36`）。
- 一筆 feed 要同時通過 rss Worker 的 `parseFeed` 和 feedparser 才能收錄。只拿到 HTTP 200 不算數：ETtoday 和遠見的日期格式 feedparser 讀不了，`RssSource` 會跳過每一篇（`src/cyris/adapters/fetch/rss_source.py:97-100`）。
- 驗證用腳本跑，不寫成 CI 測試，因為測試的對象是程式碼，不是 RSS 內容。
- 2026-10-18 從本機實測，兩種解析器都讀得了、8 天內有新文章的有：
  - 繁中：中央社政治與科技（`feeds.feedburner.com/rsscna/*`）、報導者、公視、自由時報、關鍵評論網、iThome、科技新報、INSIDE、BBC 中文繁體、紐時中文繁體、RFI 繁體、明報、RTHK、經濟日報、換日線、電腦王阿達。
  - 英文：BBC、NYT、Guardian、NPR、The Verge、Ars Technica、Economist、FT、Bloomberg、hnrss。
- 同一次實測不可用的有：
  - 中時、數位時代、端傳媒回 404。
  - udn 即時的條目是空的。
  - 天下雜誌最新一篇是約 5 年前。
  - 風傳媒用相對連結，Worker 會把相對路徑當成 URL 存進去（`workers/rss/src/parse.js:49-61`）。
  - Reuters 回 404，AP 回 403。
  - Google News 和 Yahoo 奇摩是聚合 feed，會破壞以 URL 去重。
- 實際收錄前要再驗一次。
- 標 `news` 的例子：中央社各分類、公視、自由時報、RTHK，以及照 owner 本機 `sources.yaml` 標法的報導者。不標的例子：iThome、科技新報、INSIDE、The Verge、Ars Technica。
- **清單依主題分組（owner 2026-10-20 決定）。** 挑來源那步先讓讀者選主題（可複選），再從選中的主題裡挑來源。主題是內容的題材，不是形式，新聞、專欄、部落格混在各主題裡。清單頂層有一張有序的主題表（`id`、`label`），每個 feed 標 1 到 2 個主題 id。`topics` 是清單自己的欄位，不是 `SourceConfig` 的，送 `POST /api/sources` 前拿掉；`tags` 照舊進 pipeline，不拿來當主題。依語言分組不採用：`SourceConfig.language` 除了預設值合併以外沒有地方讀它（`src/cyris/config.py:564-565`），依輸出語言篩選又會藏掉讀者會看的 feed。主題表與每個 feed 的主題目前是暫定分配，由研究清單內容的另一條線換掉。

**onboarding 精靈**
- **頁面照 `/labels` 的做法。** 在容器裡掛一條路由和一個模板（`src/cyris/entrypoints/triage_server.py:221-222`），並把 `/onboarding` 加進 app Worker 的 `PROTECTED`（`workers/app/src/router.js:16-22`）。
- **設定步驟沿用 /settings 現有的 API。** 這些 API 是 `/api/settings/values`、`/api/settings/schedule`、`/api/settings/notify`、`/api/settings/email` 和 `/api/settings/vote-similarity`（`triage_server.py:194-200`），所以精靈寫進去的值和一般表單寫進去的值一樣。
- **輸出語言和時區排第一。** 這兩項要在第一期之前定好，否則第一期會用錯的語言或錯的時段。
- **每一步都能跳過。** 跳過的設定維持預設值，之後在 /settings 改。
- **三個子步驟各自成卡。** 挑來源、投票卡片、第一期是精靈裡的三步，實作在精靈的外框之後。

**onboarding 的投票與寫入**
- **寫入順序。** 按過的文章先存進 `stored_articles`，再經 `record_votes` 投票，因為 `accept` 和 `reject` 只能更新已經存在的列（`src/cyris/adapters/store/d1_store.py:238-245`、`src/cyris/adapters/promotions.py:65-79`）。
- **在容器內直接寫。** 寫法照 `/labels`，不經 promote Worker（`src/cyris/entrypoints/triage_server.py:1392-1399`）。
- **沒碰的文章。** 它們留在 rss 緩衝區，第一次 run 照常當候選。
- **投過票的文章。** seed 的 URL 會被排除在候選之外（`src/cyris/service_layer/vote_similarity.py:89-93`）。run 只讀 pending 的文章（`src/cyris/service_layer/run_digest.py:488`），所以投過票的文章不會出現在第一期。
- **等待上限。** `/poll` 一次抓 4 個 feed，每個逾時 20 秒，一批一批跑（`workers/rss/src/index.js:40-42`、`:91-104`），所以 12 個 feed 以內最多等 60 秒。
- **只在 D1 部署上跑。** 沒有 source store 時 `POST /api/sources` 回 409（`triage_server.py:703-707`）。

**排序延伸到新聞分群**
- 把分群成員的 URL 放進偏好計算。目前偏好計算只算頭條和 Features（`src/cyris/service_layer/digest_pipeline.py:281-291`）。
- 分群在截斷前用現有的 `rank_by_preference` 排序，以分群裡分數最高的成員代表整個分群。目前分群不排序就截斷（`src/cyris/domain/selection.py:241`）。
- 分群和頭條之間的配額不變：分群的優先順序是 2，頭條是 4（`src/cyris/domain/selection.py:233`、`:255`）。
- 沒有票、或 vote similarity 被跳過時，維持模型原本的順序（`src/cyris/service_layer/vote_order.py:104-111`）。
- `docs/architecture.md:365-377` 要在同一個改動裡更新。落地後要在 §7 補一列。

**空來源要改的地方**
- `NoSourcesError` 讓 run exit 1（`src/cyris/config.py:488-503`）。
- `--if-due` 每個整點發失敗通知（`src/cyris/entrypoints/cli.py:198-208`）。
- doctor 判失敗（`src/cyris/diagnostics/doctor.py:53-54`）。
- 試用精靈第 14 步死在 feeds 0（`scripts/trial-wizard.sh:670`）。
- `/api/sources` 的 DELETE 拒絕刪掉最後一個來源（`triage_server.py:771-781`）。

這些都要改成「等待 onboarding」：run 正常結束、不發通知，doctor 回報等待中、不判失敗。

### 討論中替 owner 定的預設（可改回）

- **入口：** /settings 偵測到 `sources` 是空表時，轉到 `/onboarding`。D1 部署不論有沒有來源，都可以直接打開 `/onboarding`；json 後端打開它會被轉回 /settings。原本「已有來源的部署打開 `/onboarding` 轉回 /settings」那條，2026-10-20 由 owner 拿掉：挑來源寫入之後，讀者在投票或第一期那步重新整理頁面，會被踢出精靈。精靈出口的 `?from=onboarding` 標記保留，因為跳過挑來源的部署走完精靈時，`sources` 還是空表。
- **vote similarity：** onboarding 結束時必須是開著的。出貨預設是關的（`cyris.toml.example:97`），關著的時候票沒有任何作用。Cloudflare 部署一定有 `workers_ai` 的 bge-m3，門檻已校準為 0.53（`src/cyris/provider_defaults.json`）。
- **卡片怎麼抽：** 每個來源固定抽幾張，從 8 天緩衝區裡比較舊的文章抽，免得把最新鮮的內容從第一期拿走。
- **第一期由誰啟動：** onboarding 最後一步呼叫 `/run`，帶一個今天不會再排程的 period，精靈第 17 步跟著拿掉（`scripts/trial-wizard.sh:716-745`）。帶的 period 如果今天稍後還會排程，那次排程會用 `INSERT OR REPLACE` 蓋掉第一期（`src/cyris/adapters/store/digests.py:26`）。
- **清單只出現一次：** 只在 `sources` 為空時出現，之後加來源走現有的單筆表單。
- **沒抓到文章的提示：** 某個來源沒抓到任何文章時，onboarding 要提示讀者。這只能用 `GET /articles` 依 `source_name` 數出來。
- **等待畫面：** 等 `/poll` 時頁面顯示載入狀態。

## 還沒決定、實作時再定的事

- **feed 的粒度。** 每家媒體收一個總 feed，還是收分類 feed。分類 feed 能讓主題偏好直接進第一期，但會更快用掉 12 個 feed 的等待預算。
- **分群用哪個成員代表。** 預設是最高分的成員，要用真實資料比較其他算法。
- **Clef 的負擔。** `preference_source=clef` 時，送進 Clef 的成員數要不要設上限，看 60 秒上限內的實測（`vote_order.py:26-28`）。
- **可調參數。** 每個來源抽幾張卡，最少要投幾票。

## 什麼會推翻這個決定

- **feed 擋 Cloudflare 出口 IP。** 「直接抓媒體自己的 feed」會在正式環境失效。探測是從本機跑的，這點還沒驗證；Substack 已經有限流 Cloudflare 的前例（`workers/rss/src/index.js:41`）。正式站 D1 的 `feed_health` 能回答其中 8 個重疊的 feed。
- **試用者在意的媒體不在名單裡。** 如果大多是數位時代、端傳媒、天下、udn 這幾家，「只收媒體自己的 feed」就不夠，要重看「每套部署自架 RSSHub 補洞」。
- **清單失效得比發版快。** 如果 onboarding 常選到抓不到文章的 feed，要重看「在專案網站上發布清單」。
- **讀者幾乎不按「沒興趣」。** 三態卡片就退化成只收正向票。
- **讀者走到一半就離開。** 如果試用者常在設定步驟停下來，就要把語言、出刊時間和通知移出精靈，只留挑來源、投票和第一期。
- **`/poll` 等太久。** 如果實際等待時間讓讀者走不完流程，要重看抓取路徑。
- **重大新聞被投票擠掉。** 如果分群排序後，颱風這類新聞被擠到後面截掉，就要加限制，例如保留模型排序前幾名的分群。
- **分群很少。** 如果每期分群很少，排序延伸到分群的效益就很小。每期產生多少分群還沒量測。
- **正式站的分群順序變差。** 那就證明全域生效的代價太高，要改成票數夠多時才排序分群。

落選的方向：

- **沒勾的文章當隱含負向票：** 違反「只有真人動作能蓋 `triaged_at`」，寫入後也和真人的 👎 分不開。
- **沒勾的文章不算訊號、只收正向票：** 第一期壓不掉任何文章。owner 選了三態卡片。
- **另存曝光紀錄：** 要多一張表，現在沒有任何東西讀它。
- **容器直接用 feedparser 抓：** 第二個解析器會寫進正式資料。兩邊 URL 一致的 spec 還是 proposed（`docs/spec/rss-parsers-agree.md`）。
- **rss Worker 新增單一 feed 路由：** 每套部署都要手動重新部署 rss Worker。
- **不即時抓，用範例標題：** 範例會過時。
- **第三方目錄：** 有授權的清單幾乎沒有繁體中文。plenaryapp/awesome-rss-feeds 的 838 筆裡只有 2 筆香港媒體，timqian/chinese-independent-blogs 約 1% 是繁體。
- **公開的 RSS hub：** rsshub.app 明言只供測試（https://docs.rsshub.app/guide/instances），其他公開的 instance 都是個人維護，沒有使用條款。
- **每套部署自架 RSSHub 補洞：** 每個部署者都要多架一個服務，hub 網址會寫死在 `sources.url`。
- **在專案網站上發布清單：** 格式會變成對外契約，而清單只讀一次，發版延遲的代價很小。
- **清單照現行標法、不改排序：** 投票對分群沒有作用，「投票沒感受」會一直存在。
- **清單一律不標 `news`：** 同一事件會被多家各報一次，每 20 篇還要多一次評分和一次過濾的 LLM 呼叫。

## Suggested skills

- `cf:cf`：這份里程碑是種子，不是合約。研究、計畫和閘門都要由 /cf 自己跑。
- `adr:adr`：「新部署不帶預設 feed、改走 onboarding」和「偏好排序延伸到新聞分群」都會改變既有行為，而且難以改回，應該各寫一份 ADR，編號從 0019 起。
