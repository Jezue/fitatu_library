# Fitatu Android 4.17.1 — API catalog (decompiled)

Generated from a blutter decompilation of `libapp.so` (Flutter 3.38.5 / Dart 3.10.4, build 40296).
Every row cites the Dart method it came from; `~` is replaced client-side by the API host
(`https://<storageLocale>.fitatu.com/api`, e.g. `pl-pl`) or, for the search client, by the search host
(default `https://fitatu.com/api`). Status: ✅ = this library already calls the same method+path.
Confidence reflects how directly the path/params were read from the assembly.

This page describes what the app sends. Where the live API was recorded later
(`tests/fixtures/api_recording.json`, see [tests/fixtures/README.md](../tests/fixtures/README.md)),
the rows below carry a "Recorded:" note; the recording wins over the decompile. The main one: the
app sends whole days to `POST ~/diet-plan/{userId}/days`, but the server does not need that. It
upserts rows by `planDayDietItemId` and keeps rows that are not sent, so a client can send only the
rows it changes ([PLANNER.md](PLANNER.md)). A "Checked live" note was probed by hand against
`pl-pl.fitatu.com` on 2026-10-13.

Endpoints: 149 · already covered: 49


## activities

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/activities` | page, limit, locale, bmr, sex | none. Query comes from SearchActivityRequest; each field is omitted when null. sex is sent as an int (Sex enum: male=1, female=2, notKnown=0, notApplicable=9). | JSON array of ActivitiesGroupDTO {letter: String, activities: List<SearchActivityItemDTO>} | Activity (training) catalog grouped alphabetically. bmr and sex let the server compute energy per activity. | `_ActivitiesApi.getActivitiesGroups` | high |

## activity-plan

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/activity-plan/{userId}/summary/custom` | fromDate (yyyy-MM-dd), toDate (yyyy-MM-dd) | none | SummaryActivitiesDTO {energy: double, activities: [{count: int, measures: [{measureId, value}]?}], summaryMeasures: [{measureId, value: double}]} | Aggregated activity summary (burned energy, per-activity counts and measures) for a date range. | `_SummaryApi._SummaryApi.getActivitiesSummary` | high |

## billing

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/billing/mobile/info` |  | PurchaseRequest JSON {id, title, transaction{type,id?,signature?,receipt?,appStoreTransaction?,purchaseToken?}, additionalData{developerPayload}} | PurchaseOwnerInfoDTO {owned: bool, demo, subscription: PurchasePlanDTO{id,startDate,endDate,planName}} | Ask backend who owns a store purchase (is it already attached to this/another account). OfferApiSourceImpl.getPurchaseOwnerInfo, used by BasePurchaseRepository / PurchaseManagerUseCase. | `_ApiClient.getPurchaseOwnerInfo` | high |
|  | POST | `~/billing/restore/google` |  | PurchaseRequest (transaction.type='android-playstore') | PurchasePlanDTO {id:int, startDate, endDate, planName} | Restore Google Play subscription to current account. AndroidPurchaseRepositoryImpl. | `_ApiClient.restoreGooglePurchase` | high |
|  | POST | `~/billing/restore/ios` |  | PurchaseRequest (transaction.type='ios-appstore') | PurchasePlanDTO | Restore App Store subscription. IosPurchaseRepositoryImpl. | `_ApiClient.restoreApplePurchase` | high |
|  | POST | `~/billing/{userId}/callback` |  | PurchaseRequest JSON (same as billing/mobile/info) | BillingCallbackDTO {ok: bool, data?: {product: String}, errorCode?: BillingCallbackErrorCode string} | Submit a completed Google Play / App Store purchase for server-side validation & activation of premium. OfferApiSourceImpl.postBillingCallback / PurchaseManagerUseCase. | `_ApiClient.postBillingCallback` | high |
|  | GET | `~/billing/{userId}/payments/{paymentId}` |  | none | PaymentCheckDTO {purchaseId (int?, SafeInt), processed: bool, paymentId, subscriptionId, planGroupType} | Poll whether a (web/payment-provider) payment was processed. OfferApiSourceImpl.getPaymentCheck (short-circuited by developer option TestingPaymentResponse). | `_ApiClient.getPaymentCheck` | high |

## category-to-products

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/category-to-products/predict` |  | JSON {text: String, top_k: int (app always 2)} | ProductCategoryPredictDTO | ML category prediction for a product name | `_ProductApi._ProductApi.predictProductCategory(PredictProductCategoryRequest body)` | high |

## consents

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/consents/{consentType}/accept` |  | JSON AcceptConsentRequest {version: int (inferred; the consent version number from versions/latest)} | void | Accept a specific version of a consent (AI_PROCESSING). | `_UserConsentApi.acceptConsent` | high |
|  | POST | `~/consents/{consentType}/revoke` |  | none | void | Revoke a consent. The only type used in the app is "AI_PROCESSING". | `_UserConsentApi.revokeConsent` | high |
|  | GET | `~/consents/{consentType}/versions/latest` |  | none | ConsentVersionTextDTO {type: String, version: int, content: {title: String, text: String}} | Get the latest consent text to display (HTML-like text with link placeholders). | `_UserConsentApi.getConsentVersionText` | high |
|  | GET | `~/v2/consents` |  | none | ConsentAggregateDTO {consents: List<ConsentStatusItemDTO{type: String, status: ConsentStatus, accepted: {version}?, latest: {version}}>} | Status of all of the user's consents. | `_UserConsentApi.getConsents` | high |

## diet-and-activity-plan

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/diet-and-activity-plan/{userId}/day/{date}` | withRating=true (always sent: const PlannerRequest@19ab131 is constant-folded, and _$PlannerRequestToJson @0xc69664 _set()s 'withRating' -> true unconditionally) | none | PlannerDTO (Map). Before parsing, PlannerDTO.fromJson copies the map, removes top-level 'note' (String?) and 'tagsIds' (List), and re-inserts them as note: {note, tagsIds}. Keys: planDayRevisions, activityPlan[], dietPlan{}, toilet[], water{}, note, tagsIds | Fetch one day's diet + activity plan, plus water, toilet and note, with recipe ratings. The app uses it on first load of a day, when no local copy exists, and to overwrite the local day when the server has unknown revisions. | `_PlannerApi._PlannerApi.getPlanner` | high |

## diet-plan

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | POST | `~/diet-plan/{userId}/days` | synchronous (bool, optional; written only when non-null by _$PlannerChangesRequestToJson; the app sends synchronous=true from forceSynchronizePlannerDay and forceSendPlannerLocalChanges, and omits it from synchronizePlannerLocalChanges) | JSON object (Map<String,dynamic>, passed as Options.compose data). Keys are dates as 'yyyy-MM-dd'. Each value is a PlannerUpdateRequest serialized by toJson: {planDayRevisions, activities, dietPlan, toilet, water, note, tagsIds}. It is a full-day snapshot with soft-deleted entries included. See DTO PlannerUpdateRequest. No Content-Type header literal, so Dio's default JSON is used. | List<PlannerSynchronizationDTO>, one per day sent: [{date: String, revision: String?, errorMessage: String?}] | Upload locally changed planner days (meals, activities, toilet, water, note) as full-day snapshots and get a new revision per day. In the app this is the only write path for diet, activity, water and notes. Recorded: the server does not need full snapshots. It upserts rows by planDayDietItemId, keeps every row that is not in the payload (a meal sent with items [] keeps its rows; a payload with only activities keeps the food rows), replaces a sent row as a whole (an omitted eaten becomes false) and hides rows sent with deletedAt. A rejected day still answers 202, with revision null and an errorMessage, and nothing is saved for it. Checked live (2026-10-14): a payload without note and tagsIds (only dietPlan, only activities, a row deletion) keeps them and the water; {"note": "text", "tagsIds": []} writes the note and note null deletes it. | `_PlannerApi (features/planner/data/api/planner_api.dart)._PlannerApi.sendPlannerSynchronization` | high |
| ✅ | GET | `~/diet-plan/{userId}/days/{date}/revisions` |  | none | List<String> of revision ids for the day (cast via List.cast<String>) | Cheap conflict check. synchronizePlannerDay compares server revisions with the local planDayRevisions and refetches the day via getPlanner only when the server has revisions the client does not know. | `_PlannerApi._PlannerApi.getPlannerRevisions` | high |
| ✅ | GET | `~/diet-plan/{userId}/export` | format (String: 'xls' \| 'pdf' \| 'txt' \| 'csv'; enum DataExportFormat excel/pdf/text/csv via const map @pp+0x3a0c8), fromDate (yyyy-MM-dd), toDate (yyyy-MM-dd) | none | HttpResponse<List<int>>: raw file bytes. Options.responseType = ResponseType.bytes. The client reads the 'content-type' response header to choose MimeKind/extension. | Download a diary export file (xls/pdf/txt/csv) for a date range so it can be shared. Recorded: 204 with an empty body for a range without entries, 422 when fromDate is after toDate. | `_DataExportApi (features/data_export/data/api/data_export_api.dart)._DataExportApi.getExportedData` | high |
| ✅ | GET | `~/diet-plan/{userId}/summary-product/{nutritionType}/custom` | fromDate (yyyy-MM-dd via DateConverter), toDate (yyyy-MM-dd), page (int; key always present), limit (int; key always present) | none | SummaryNutritionProductsDTO {list: [{name: String, amount: {current: num, per100: num?}}], count: int} | Products that contributed most to a nutrient over a date range (nutrition details screen), paginated. | `_SummaryApi (features/summary/data/api/summary_api.dart)._SummaryApi.getNutritionProductsSummary` | high |
| ✅ | GET | `~/v2/diet-plan/{userId}/summary/custom` | fromDate (yyyy-MM-dd), toDate (yyyy-MM-dd) | none | SummaryNutritionsDTO: per-nutrient objects. energy:{min,max,current}; dietEnergy/activityEnergy/addedActivityEnergy:{max}; protein/fat/carbohydrate:{min,max,current} (macro subclasses); all other nutrients {min,max,current} | Overall nutrition summary (averages and targets) over a date range. | `_SummaryApi._SummaryApi.getNutritionsSummary` | high |
| ✅ | GET | `~/v2/diet-plan/{userId}/summary/{nutritionType}/custom` | fromDate (yyyy-MM-dd), toDate (yyyy-MM-dd) | none | SummaryNutritionDetailsDTO {measures: {'<date>': double?}, targets: {'<date>': double}? (nullable map), targetRanges: {'<date>': {min: double?, max: double?}}} | Per-day consumption, target and target range of one nutrient over a date range (chart). | `_SummaryApi._SummaryApi.getNutritionDetailsSummary` | high |

## external-apps

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/external-apps/garmin/unauthorized-token` |  | none | GarminTokensDTO {token:String, tokenSecret:String} | Garmin OAuth1 step 1: get request token/secret; app then opens https://connect.garmin.com/oauthConfirm?oauth_token={token}&oauth_callback=fitatu://app/fitness-app-auth/garmin | `_ConnectedAppsApi._ConnectedAppsApi.getGarminTokens()` | high |
|  | DELETE | `~/external-apps/garmin/{userId}/authorization` |  | none | void | Unlink Garmin | `_ConnectedAppsApi._ConnectedAppsApi.deauthorizeGarmin(String userId)` | high |
|  | POST | `~/external-apps/garmin/{userId}/authorization` |  | JSON {"verifier": String (oauth_verifier from callback), "token": String, "tokenSecret": String} | void | Garmin OAuth1 step 2: hand verifier + request token to backend to finish linking | `_ConnectedAppsApi._ConnectedAppsApi.authorizeGarmin(String userId, GarminAuthorizationRequest request)` | high |
|  | GET | `~/external-apps/garmin/{userId}/authorization/status` |  | none | ConnectedAppsAuthorizationDto {authorized: bool} | Check whether Garmin is linked | `_ConnectedAppsApi._ConnectedAppsApi.getGarminAuthorization(String userId)` | high |
|  | DELETE | `~/external-apps/huawei/healthkit/{userId}/authorization` |  | none | void | Unlink Huawei Health | `_ConnectedAppsApi._ConnectedAppsApi.deauthorizeHuaweiHealth(String userId)` | high |
|  | POST | `~/external-apps/huawei/healthkit/{userId}/authorization` |  | JSON {"code": String} | void | Huawei Health Kit OAuth2: exchange code from https://oauth-login.cloud.huawei.com/oauth2/v3/authorize?client_id=104191503&response_type=code&scope=openid+...healthkit/{step,distance,speed,calories,activity}.read&redirect_uri=https://www.fitatu.com/app/fitness-app-auth/huawei&access_type=offline&display=page | `_ConnectedAppsApi._ConnectedAppsApi.authorizeHuaweiHealth(String userId, HuaweiHealthAuthorizationRequest request)` | high |
|  | GET | `~/external-apps/huawei/healthkit/{userId}/authorization/status` |  | none | ConnectedAppsAuthorizationDto {authorized: bool} | Check Huawei Health link status | `_ConnectedAppsApi._ConnectedAppsApi.getHuaweiHealthAuthorization(String userId)` | high |
|  | DELETE | `~/external-apps/polar-flow/{userId}/authorization` |  | none | void | Unlink Polar Flow | `_ConnectedAppsApi._ConnectedAppsApi.deauthorizePolarFlow(String userId)` | high |
|  | POST | `~/external-apps/polar-flow/{userId}/authorization` |  | JSON {"code": String (OAuth2 code), "redirectUrl": "fitatu://app/fitness-app-auth/polar"} | void | Polar Flow OAuth2: exchange authorization code (from https://flow.polar.com/oauth2/authorization?client_id=13183b7f-6429-494b-a577-4ee0359ee0c3&response_type=code&redirect_uri=fitatu://app/fitness-app-auth/polar) | `_ConnectedAppsApi._ConnectedAppsApi.authorizePolarFlow(String userId, PolarFlowAuthorizationRequest request)` | high |
|  | GET | `~/external-apps/polar-flow/{userId}/authorization/status` |  | none | ConnectedAppsAuthorizationDto {authorized: bool} | Check Polar Flow link status | `_ConnectedAppsApi._ConnectedAppsApi.getPolarFlowAuthorization(String userId)` | high |
|  | DELETE | `~/external-apps/strava/{userId}/authorization` |  | none | void | Unlink Strava | `_ConnectedAppsApi._ConnectedAppsApi.deauthorizeStrava(String userId)` | high |
|  | POST | `~/external-apps/strava/{userId}/authorization` |  | JSON {"code": String} (StravaAuthorizationRequest.toJson is code-folded with _$HuaweiHealthAuthorizationRequestToJson -> identical single key "code") | void | Strava OAuth2: exchange code from https://www.strava.com/oauth/authorize?client_id=43054&response_type=code&scope=read,activity:read_all&approval_prompt=force&redirect_uri=https://www.fitatu.com/app/fitness-app-auth/strava | `_ConnectedAppsApi._ConnectedAppsApi.authorizeStrava(String userId, StravaAuthorizationRequest request)` | high |
|  | GET | `~/external-apps/strava/{userId}/authorization/status` |  | none | ConnectedAppsAuthorizationDto {authorized: bool} | Check Strava link status | `_ConnectedAppsApi._ConnectedAppsApi.getStravaAuthorizationStatus(String userId)` | high |

## feedback

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/feedback/search` |  | UserSearchFeedbackRequest {locale, rating, searchQuery, module, version, searchResults:[{foodId, foodType, foodName}], searchFilters:{minEnergy,maxEnergy,minProtein,maxProtein,minFat,maxFat,minCarbohydrate,maxCarbohydrate}, topic, explanation} | void | User feedback on food-search quality (sent on main API, not search host). SearchApiSourceImpl.sendUserFeedback <- SearchRepositoryImpl. | `_ApiClient.sendUserFeedback` | high |

## food-proposal

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/food-proposal/plan/{planId}` |  | none | MenuPlanDTO (see the DTO list) | Details of a single menu plan | `_MenuPlanApi.getMenuPlan` | high |
|  | GET | `~/food-proposal/plans` |  | none | MenuPlansDTO {popular: List<MenuPlanDTO>, elimination: List<MenuPlanDTO>, all: List<MenuPlanDTO>} | List of menu (diet) plans, grouped | `_MenuPlanApi.getMenuPlans` | high |

## foodshares

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/foodshares` |  | FoodShareRequest {userId: int, mealsDate: String, items: [FoodShareItemDTO{meal, measureId, measureQuantity, type?, itemId?, measureWeight?, ingredientsServing?, customItem?}]} | FoodShareResponseDTO {hash: String} | Create a share link for meals of a day; returns hash. FoodShareApiSourceImpl.shareFood (returns Either<Failure,String>) <- ShareFoodUseCase. | `_ApiClient.shareFood` | high |
|  | GET | `~/foodshares/{hash}` |  | none | SharedFoodDTO {mealsDate: String, items[SharedFoodItemDTO{name, measureId, measureQuantity, meal(MealType), type(FoodType), measureName?, itemId?, measureWeight?, ingredientsServing?, customItem?{name,energy,energyCalories,protein,fat,carbohydrate}}]} | Open a shared-meals link (food share) to import foods. FoodShareApiSourceImpl.getSharedFood <- GetSharedFoodUseCase. | `_ApiClient.getSharedFood` | high |

## game

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/game/active-days` | from, to | none. Query comes from GameActiveDaysRequest: from and to are Date values formatted by DateConverter as yyyy-MM-dd. | GameActiveDaysResponseDTO {days: List<GameActiveDayDTO{date: String yyyy-MM-dd, type: GameActiveDayTypeDTO}>} | Calendar of active, quest-completed and shield-protected days for the streak view. | `_GamificationApi.getActiveDays` | high |
|  | GET | `~/game/quests` | from, to | none. Query params come from GameStateRequest via @Queries: from and to are DateTime values, each sent as DateTimeTimezoneConverter.toJson = dt.toUtc().toIso8601String(), e.g. 2026-10-13T00:00:00.000Z. | GameStateDTO {content: List<GameQuestDTO{id, type, status, endDate, startDate, actionList:[{type}], rewardCollected(bool), rewardTypes:[GameRewardType]}>} | Fetch the quest state for a date range. | `_GamificationApi.getGameQuests` | high |
|  | PUT | `~/game/quests` |  | JSON array: List<GameQuestDTO> mapped via _$GameQuestDTOToJson -> [{id, type, status, endDate, startDate, actionList:[{type}]}]. Passed as `data` (r5 = fp-0x18, a list built by .map(closure 0xd9639c).toList()). | Future<void> (body ignored) | Push the client's quest state (daily quest progress/status) to the server. The source layer skips the call if the payload equals the last successful one. | `_GamificationApi.updateGameQuests` | high |
|  | POST | `~/game/reward-boxes/collect-all` |  | none (data=null) | GameRewardsCollectedDTO {rewards: List<{type: GameRewardType, jewels: int}>} | Collect all pending reward boxes. | `_GamificationApi.collectAllRewardBoxes` | high |
|  | GET | `~/game/reward-boxes/rules/daily` |  | none | GameRewardBoxRulesDTO {rules: {daily: List<{type: BASE\|MILESTONE\|RECURRING, jewels, streakDay?, streakFrom?, repeatEvery?}>}} | Static config: which daily or streak milestones award reward boxes and how many jewels each gives. | `_GamificationConfigApi.getRewardBoxesDailyRules` | high |
|  | GET | `~/game/reward-boxes/to-collect` |  | none | GameRewardBoxesToCollectDTO {boxes: List<{type: GameRewardType, jewels: int, createdAt: String}>} | List the reward boxes waiting to be collected. | `_GamificationApi.getRewardBoxesToCollect` | high |
|  | GET | `~/game/store/products` |  | none | GameStoreProductsDTO {content: List<GameStoreProductDTO{productId: String, price: {currency: GameCurrencyType, amount}, items: List<{amount, type: GameStoreProductItemType, resourceType: GameStoreProductItemResourceType}>}>} | Gamification store catalog. | `_GamificationStoreApi.getStoreProducts` | high |
|  | POST | `~/game/store/purchase` |  | JSON GameStorePurchaseRequest {productId: String, quantity: int (inferred)} | GameStorePurchaseDTO {currencies: Map<GameCurrencyType key, GameCurrencyDTO{type, amount}>, resources: Map<UserGameResource key, UserGameResourceDTO{type, amount, currentLimit, maxLimit}>} | Spend jewels on a store product, for example buying a streak shield. | `_GamificationStoreApi.purchaseProduct` | high |
|  | GET | `~/game/user/profile` |  | none | UserGameProfileDTO {currencies: Map<String, GameCurrencyDTO{type, amount}>, resources: Map<String, UserGameResourceDTO{type, amount, currentLimit, maxLimit}>, streak: UserGameStreakDTO{current, longest, date, startDate}} | Game wallet/profile: currency balances (jewels), resources (shields), and the streak. | `_GamificationApi.getUserProfile` | high |

## habits

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/habits/{userId}` |  | none | List<HabitDTO {id: int, name: String, translationKey: String, length: int (inferred), premium: bool}> | Habit catalog available to the user. Checked live (2026-10-14): 22 habits, name in Polish, length in days. | `_HabitApi.getHabits` | high |
| ✅ | GET | `~/habits/{userId}/good-days` |  | none | List<String> (dates; the source maps them to List<Date>) | List of 'good days' (habit streak tab in summary). | `_SummaryApi._SummaryApi.getGoodDays` | high |

## health

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/health` |  | none | void | Ping of the search API ("Failed to while contacting API's search ping endpoint") | `_SearchApi._SearchApi.healthCheck()` | high |

## intermittent-fasting

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/intermittent-fasting/{userId}/periods` | date (yyyy-MM-dd, required) | none | List<FastingPeriodDTO {id: String, date: String, startDate: String, endDate: String, default: bool, deleted: bool}> | Fasting periods for a given day | `_FastingApi.getFastingPeriods` | high |
|  | POST | `~/intermittent-fasting/{userId}/periods` |  | JSON ARRAY of FastingPeriodRequest: [{id, date, startDate, endDate, default, deleted, isCreatedDynamically}]. The app always sends a 1-element list. | void | Create, update or soft-delete fasting periods (eating windows) | `_FastingApi.updateFastingPeriods` | high |
| ✅ | GET | `~/intermittent-fasting/{userId}/summary` | from (yyyy-MM-dd, required), to (yyyy-MM-dd, required) | none | List<FastingSummaryDTO {date: String, eatingPeriods: List<FastingEatingPeriodDTO>, expectedEatingPeriods: List<FastingEatingPeriodDTO>}>; FastingEatingPeriodDTO {date: String, from: String (time, Time.parse), to: String (time)} | Per-day intermittent-fasting summary (actual vs expected eating windows) for a date range. Checked live (2026-10-14): an empty list for days without fasting. | `_FastingApi.getSummary` | high |
| ✅ | GET | `~/intermittent-fasting/{userId}/types` |  | none | List<FastingDTO {id: int (SafeInt), symbol: FastingType string, currentlyInUse: double (SafeDouble), rating?: double, reviewsPerLocale: List<{count: int, locale: String}>}> | Catalog of fasting types/schemes (14:10, 16:8, 18:6, 20:4, other) with usage and ratings. Checked live (2026-10-14): 9 types, symbols 14_10_hours, 16_08_hours, 18_06_hours, 20_04_hours, 5_2_days, 6_1_days, mwf_days, tts_days, custom, with id, currentlyInUse, rating, ratingCount, reviewsPerLocale. | `_FastingTypesApi.getFastings` | high |

## label-scanner

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/label-scanner/session/process_photo/{imageType}` |  | multipart/form-data (no explicit contentType in Options, but FormData => multipart). Parts: "file" (MultipartFile.fromBytes(bytes, filename: request.fileName, contentType: image/jpeg)); text field "session_id". imageType path segment is LabelScanType value: "front" \| "nutrients" \| "ingredients". | void (no body parsed) | Upload one label photo (front of pack / nutrition table / ingredient list) into the session | `_LabelScanApi._LabelScanApi.processPhoto(String imageType, List<MultipartFile> files, String sessionId)` | high |
|  | POST | `~/label-scanner/session/results` |  | JSON {"session_id": String} | LabelScanResultDTO {session_id:String, codename:LabelScanStatus, session_data:LabelScanSessionDataDTO, merged_portions:[LabelScanMergedPortionDTO], base_type:LabelScanBaseType (unknownValue=default)} | Poll OCR/extraction results for the session (app polls until done, then may resend failed steps) | `_LabelScanApi._LabelScanApi.sessionResults(LabelScanSessionIdRequest request)` | high |
|  | POST | `~/label-scanner/session/save_completed_session` |  | JSON LabelScanSaveCompletedSessionRequest {session_id, front_modified_json:{product_name, brand_name}, ingredients_modified_json:{ingredients}, nutrients_modified_json:{base_value, base_unit, energy:{value}, protein:{value}, ... carbohydrateExchangers:{value}}, merged_portions_modified:[{portion_name, portion_weight, portion_unit}], modified_front:bool, modified_nutrients:bool, modified_ingredients:bool, modified_merged_portions:bool} | void | Save the user-edited/confirmed label scan result (feedback on what user changed) | `_LabelScanApi._LabelScanApi.sessionCompleted(LabelScanSaveCompletedSessionRequest request)` | high |
|  | POST | `~/label-scanner/session/skip_photo/{imageType}` |  | JSON LabelScanSessionIdRequest {"session_id": String} | void | Mark a label photo step (front\|nutrients\|ingredients) as skipped | `_LabelScanApi._LabelScanApi.skipPhoto(String imageType, LabelScanSessionIdRequest request)` | high |
|  | POST | `~/label-scanner/session/start` |  | none | LabelScanSessionStartDTO {session_id: String} | Start a nutrition-label scanning session | `_LabelScanApi._LabelScanApi.sessionStart()` | high |

## login

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | POST | `~/login` |  | JSON SignInByEmailRequest: {"_username": email, "_password": password, "settings"?: ProfileSetupRequest{userSettings,userDietSettings,weekMealSchema}}. 'settings' is only sent when non-null. | AuthTokens {token, refreshToken\|refresh_token, userDemo?} | Email/password login (Symfony-style _username/_password). The returned JWT is decoded locally (WebToken) to get the user id. Checked live (2026-10-13): a wrong or unknown e-mail or password, and a body without _password, answer 422 {"code": 422, "message": "Username or password are invalid"}, not 401; an empty body 400 "Bad Request"; no API-Key/API-Secret 403 "Invalid API-Key/API-Secret". The same on https://fitatu.com/api/login, without redirects. | `_UnauthorizedClient._UnauthorizedClient.signInByEmail` | high |

## login-demo

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/login-demo` |  | JSON SignInGuestRequest: {locale, searchLocale, settings: ProfileSetupRequest, acceptTerms, sensitiveAccepted, marketingAccepted, acceptAgeRequirements} | AuthTokens (userDemo holds a JSON string {"password": ...} for the generated demo account) | Create a guest (demo) account from onboarding data. Used by AuthRepositoryImpl.signUpGuest. | `_UnauthorizedClient._UnauthorizedClient.signInGuest` | high |

## login-facebook

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/login-facebook` |  | JSON SignInByFacebookRequest: {email?, facebookUserAccessToken, facebookUserId, locale, searchLocale?, partnerId: "07b92206-189c-11e8-81d0-0242ac120007", tokenType?: "LIMITED"\|"FULL", settings?: ProfileSetupRequest, acceptTerms, sensitiveAccepted, marketingAccepted, acceptAgeRequirements} | AuthTokens. On a failure body {"validation": {acceptTerms\|sensitiveAccepted\|email: ...}} the app maps it to oAuthNeedsSignUp / oAuthMissingSignUpEmail. | Facebook sign-in or sign-up. createSignInRequest and createSignUpRequest both set the partnerId constant. | `_UnauthorizedClient._UnauthorizedClient.signInByFacebook` | high |

## photo-predictor

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/photo-predictor/insert_completed` |  | AddPhotoAiEstimationSuccessfullyRequest {prediction_id, feedback_id, used_items:[{foodId:int, name, weight, type:FoodType string, energy, protein, fat, carbohydrate, is_alternative}], modified_ingredients, modified_weights} | void | Report accepted photo-AI prediction. AddPhotoAiEstimationSuccessfullyUseCase. | `_ApiClient.addPhotoAiEstimationSuccessfully` | high |
|  | POST | `~/photo-predictor/predict` |  | multipart/form-data (Options.contentType literal). Parts in this order: file part(s) named "file" (one per MultipartFile; the app sends exactly one, built with MultipartFile.fromBytes(bytes, filename: request.fileName, contentType: image/jpeg)); text field "locale" = locale; text field "bbox" = jsonEncode(frameInfo) i.e. a JSON string {"x":double,"y":double,"width":double,"height":double}; text field "is_from_gallery" = "true"\|"false" (bool stringified). Raw binary upload, not base64. All fields unconditional (no null checks). | PhotoAiEstimationPredictedDTO {prediction_id:String, feedback_id:String?, food_name:String?, reasoning:String?, ingredients_search:[PhotoAiEstimationPredictedIngredientDTO], image_url:String?, image_id:int, recipe_id:int, model_release_version:double} | Photo AI meal estimation: upload a JPEG of a meal and get predicted dish name + ingredient list with nutrients | `_AiEstimationPredictApi._AiEstimationPredictApi.predict(List<MultipartFile> files, String locale, String bbox, bool isFromGallery)` | high |
|  | POST | `~/photo-predictor/predict_with_user_feedback` |  | multipart/form-data. Parts in order: "file" (image/jpeg bytes, filename=request.fileName); "locale"; "bbox" = JSON string {x,y,width,height}; "previous_prediction" = JSON string of PhotoAiEstimationPreviousPrediction {prediction_id, food_name, reasoning, image_url, image_id, recipe_id, ingredients:[{name, weight, is_alternative, nutrients:{energy, protein, fat, carbohydrate}}]}; "user_feedback" = free-text String. All required (MapEntry<String,String> type checks, no null guards). | PhotoAiEstimationPredictedDTO (same as predict) | Re-run photo estimation with the user's correction text (clarify flow) | `_AiEstimationPredictApi._AiEstimationPredictApi.predictWithUserFeedback(List<MultipartFile> files, String locale, String bbox, String previousPrediction, String userFeedback)` | high |
|  | GET | `~/photo-predictor/user-info` |  | none; header X-Fitatu-Test-Group: <testGroup> (removed if null) | AiInfoDTO {limit_prediction: int, counter_prediction: int, model_release_version: double?} | Photo-AI scan quota/limits for the user. AIEstimationApiSourceImpl.getPhotoAiEstimationInfo via AiEstimationGrommet. | `_ApiClient.getPhotoAiEstimationInfo` | high |

## ping

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | HEAD | `~/ping` |  | none | void (status only) | API health check used by emergency/technical-break screen (PingApiUseCase via ApplicationApiSourceImpl). Uses the unauthorized Dio (no AuthInterceptor) but still ApiClientUrlsInterceptor. | `_PingApi.ping` | high |

## product-ingredients

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/product-ingredients/{ingredientId}` |  | none | ProductIngredientDTO | Ingredient/additive details (E-number info, health rating) shown from product score/ingredients | `_ProductApi._ProductApi.getProductIngredient(String ingredientId)` | high |

## products

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | POST | `~/products` |  | JSON ProductFormRequest | ProductCreatedDTO {id:int} | Create product | `_ProductApi._ProductApi.addProduct(ProductFormRequest body)` | high |
| ✅ | DELETE | `~/products/{productId}` |  | none | void | Delete user product | `_ProductApi._ProductApi.deleteProduct(int productId)` | high |
| ✅ | GET | `~/products/{productId}` |  | none | HttpResponse<ProductDTO> | Product details | `_ProductApi._ProductApi.getProduct(int productId)` | high |
|  | PATCH | `~/products/{productId}` |  | JSON FillIncompleteProductPropertiesRequest {energy, fat, carbohydrate, protein, saturatedFat, sugars, fiber, salt} | FillIncompleteProductPropertiesDTO {id:int} | Fill missing nutrition values of a product | `_ProductApi._ProductApi.fillIncompleteProductProperties(int productId, FillIncompleteProductPropertiesRequest body)` | high |
| ✅ | PUT | `~/products/{productId}` |  | JSON ProductFormRequest (47 keys, nulls included) | ProductUpdatedDTO {id:int} | Edit user product | `_ProductApi._ProductApi.updateProduct(int productId, ProductFormRequest body)` | high |
|  | POST | `~/products/{productId}/action/report` |  | JSON {message: String} | void | Report a product error | `_ProductApi._ProductApi.reportProduct(int productId, ReportProductRequest body)` | high |
|  | POST | `~/products/{productId}/barcodes` |  | JSON {barcode: String} | void | Attach a barcode to a product | `_ProductApi._ProductApi.addProductBarcode(int productId, AddProductBarcodeRequest body)` | high |
| ✅ | POST | `~/products/{productId}/proposals` |  | JSON {propertyName: "rawIngredients"\|"category"\|"undefined", propertyValue: value as-is} | Future<dynamic> (body ignored) | Propose a value for a product property | `_ProductApi._ProductApi.addProductProposalProperty(int productId, AddProductPropertyProposalRequest body)` | high |
|  | POST | `~/products/{productId}/proposals/{proposalId}/reviews` |  | JSON {accepted: int 1\|0} | void | Accept/reject a property proposal on a product | `_ProductApi._ProductApi.postProductProposalReview(int productId, String proposalId, PostProductProposalReviewRequest body)` | high |

## rating

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | PUT | `~/rating/{reviewType}/{itemId}` |  | AddReviewRateRequest {rate: int} | void | Star-rate an item (product/recipe/...). ReviewApiSourceImpl.addRate (then deletes recipe from cache). | `_ApiClient.addReviewRate` | high |

## recipes

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | POST | `~/recipes` |  | JSON UpdatedRecipeRequest | UpdatedRecipeDTO | Create recipe | `_RecipeApi._RecipeApi.addRecipe(UpdatedRecipeRequest body)` | high |
| ✅ | DELETE | `~/recipes/{recipeId}` |  | none | void | Delete recipe | `_RecipeApi._RecipeApi.deleteRecipe(int recipeId)` | high |
| ✅ | PUT | `~/recipes/{recipeId}` |  | JSON UpdatedRecipeRequest | UpdatedRecipeDTO | Update recipe. Not checked live yet: `client.update_recipe` sends the create body whole, as the app does. | `_RecipeApi._RecipeApi.putRecipe(int recipeId, UpdatedRecipeRequest body)` | high |
|  | POST | `~/recipes/{recipeId}/action/report` |  | JSON {message: String} | void | Report recipe error | `_RecipeApi._RecipeApi.reportRecipe(int recipeId, ReportRecipeRequest body)` | high |

## recipes-ai

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/recipes-ai` |  | AddPhotoAiEstimationRequest {predictionId, name, descriptionAi, items:[{itemId:int, measureId:int, measureQuantity:double}]} | PhotoAiEstimationAddedDTO {id} | Save a photo-AI prediction as a RECIPE_AI item. AddPhotoAiEstimationUseCase. | `_ApiClient.addPhotoAiEstimation` | high |
|  | GET | `~/recipes-ai/{id}` |  | none | PhotoAiEstimationDTO {id, name, photos[{id,path}], recipeAI{id, aiModel}, items[{itemId, name, measureQuantity, nutrients{energy,protein,...}, type(FoodType)}], userId} | Load a saved photo-AI meal estimation (a 'RECIPE_AI' food item). AIEstimationApiSourceImpl.getPhotoAiEstimation via AiEstimationGrommet. | `_ApiClient.getPhotoAiEstimation` | high |
|  | PUT | `~/recipes-ai/{id}` |  | UpdatePhotoAiEstimationRequest {name, items:[{itemId:int, measureId:int, measureQuantity:double, type:FoodType string}]} | PhotoAiEstimationAddedDTO {id} | Edit a saved photo-AI meal (name/ingredients). UpdatePhotoAiEstimationUseCase. | `_ApiClient.updatePhotoAiEstimation` | high |

## recipes-and-user-action

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/recipes-and-user-action/{recipeId}/{userId}` |  | none | HttpResponse<RecipeDetailsDTO> | Recipe details incl. user-specific state | `_RecipeApi._RecipeApi.getRecipe(int recipeId, String userId)` | high |

## recipes-catalog

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/recipes-catalog` | foodProposalPlanId (optional, omitted when null) | none | RecipeCatalogDTO | Recipe catalog home (all categories with recipes) | `_RecipeApi._RecipeApi.getRecipeCatalog(RecipeCatalogRequest request)` | high |
| ✅ | GET | `~/recipes-catalog/category/{categoryId}` | recipesOffset (int, required), recipesLimit (int; constant-folded to 25 in this build), foodProposalPlanId (optional, removed when null) | none | RecipeCategoryDTO | Paged recipes of one catalog category | `_RecipeApi._RecipeApi.getAllRecipesCategory(String categoryId, int recipesOffset, int recipesLimit, foodProposalPlanId?)` | high |

## resolutions

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/resolutions` |  | none | JSON array of ResolutionDTO {id, name, translationTag, createdAt, userValue?} | Catalog of predefined resolutions. | `_ResolutionsApi.getResolutions` | high |
|  | GET | `~/resolutions/statistics/simple` |  | none | ResolutionsSimpleStatsDTO {countAllUsersUsedResolutions: int} | Global counter of how many users have set resolutions. | `_ResolutionsStatsApi.getSimpleStats` | high |

## resources

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/resources/food-tags/recipe` |  | none | List<PlanRecipeTagDTO> | Recipe tag dictionary. Checked live (2026-10-15): 60 entries of {name: the Polish label, id: the category}; no tag.* key. | `_ResourcesApi._ResourcesApi.getRecipeTags()` | high |
|  | POST | `~/resources/media-moderation/{mediaId}/report` |  | none (data=null) | void | Report inappropriate media | `_ResourcesApi._ResourcesApi.reportMedia(int mediaId)` | high |
|  | POST | `~/resources/media/{entityType=recipe}/{entityId}` |  | multipart/form-data; each file as field "file" (FormData.files.addAll(files.map(f => MapEntry('file', f)))) | HttpResponse<dynamic> (JSON object; source wraps Map in MediaUploadResultSuccess) | Upload recipe photos | `_ResourcesApi._ResourcesApi.uploadMedia(int entityId, List<MultipartFile> files, ProgressCallback onSendProgress) [entityType const "recipe"]` | high |
|  | DELETE | `~/resources/media/{entityType=recipe}/{entityId}/{mediaId}` |  | none | HttpResponse<void> | Delete a photo of a recipe | `_ResourcesApi._ResourcesApi.deleteMedia(int entityId, int mediaId) [entityType const "recipe"]` | high |

## reviews

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/reviews/{reviewType}` | itemId (int), page (int), limit (always 20), locale | none | ReviewsDTO {count, lastUserReviewId?, locales: Map<String,int>, reviews:[ReviewDTO]} | List reviews for a product/recipe/diet/plan item. ReviewApiSourceImpl.get <- ReviewRepositoryImpl.get. | `_ApiClient.getReviews` | high |
|  | POST | `~/reviews/{reviewType}` |  | AddReviewContentRequest {content: String, itemId: int} | void | Write a new review. ReviewApiSourceImpl.addContent. | `_ApiClient.addReviewContent` | high |
|  | GET | `~/reviews/{reviewType}/{reviewId}` |  | none | ReviewDTO {id, itemId, content, locale, typeId(int), visible, authorGender, nickname, userId?, createdAt, updatedAt, rating} | Fetch single review (e.g. user's own). ReviewApiSourceImpl.getItem. | `_ApiClient.getReview` | high |
|  | PATCH | `~/reviews/{reviewType}/{reviewId}` |  | UpdateReviewContentRequest {content: String} | void | Edit text of own review. ReviewApiSourceImpl.updateContent. | `_ApiClient.updateReviewContent` | high |

## sales

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/sales/purchase/list/v2` | accessGroupCode[], testing | none. accessGroupCode[] is a list of access group codes (List<String>, inferred) and may be repeated. testing is a bool? from developer options. Null values are removed. | PaymentProductsDTO {plans: List<PaymentPlanDTO>, promotions: List<PromotionDTO>, minPrices: List<MinPricePlanDTO>} | List purchasable premium plans, active promotions and minimum prices for the paywall. | `_SalesApi.getPaymentProducts` | high |

## search

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/search/activity/{phrase}` | page?, limit?, locale?, bmr?, sex? (int: 0=notKnown,1=male,2=female,9=notApplicable) | none | List<SearchActivityItemDTO> {activityId, name, type(int 0..3), intensity(int 1..4), energy(double), measuresDescription, measures:[ActivityMeasureDTO{measureId,name,short,type,scale,selected,value,defaultValue}], uuid?} | Search activity/training catalog (on main API host via ApiClient). GetSearchActivitiesUseCase <- DailyGoalTrainingSearchBloc. | `_ApiClient.getSearchActivities` | high |
| ✅ | GET | `~/search/activity/{userId}/last-used` | page?, limit?, locale?, bmr?, sex? | none | List<SearchActivityItemDTO> | User's recently used activities (cached locally afterwards). GetLastUsedUsersActivitiesUseCase. | `_ApiClient.getLastUsedUsersActivities` | high |
| ✅ | GET | `~/search/catalog/recipes` | page, limit, hasFilters, locale, phrase, + filter-derived keys (tags[], meals[], ingredients[], source[], minEnergy.., minCookingTime/maxCookingTime, type) | none | List<SearchRecipeCatalogItemDTO> | Recipe catalog search. Checked live (2026-10-15): hits in the food search's shape with type RECIPE, energy and measure of one portion, premiumInRecipesCatalog, rating, cookingTime, preparationTime. min/max Energy, Protein, Fat, Carbohydrate filter per portion and take whole numbers only (a decimal is 400); maxCookingTime filters cookingTime in two steps (10-29 -> 10, 30 and up -> 30, below 10 none); minCookingTime is ignored; tags[] (tag.* names) are ANDed, meals[] ORed; source[] 1 community, 2 Fitatu, 4 Fitatu dietitians; no sorting or rating filter; limit caps at 200; filters apply with or without hasFilters=true (2026-10-14 they needed it). | `_SearchApi._SearchApi.getCatalogRecipes(SearchFoodRequest request)` | high |
|  | GET | `~/search/food-proposal` | page, limit, energy, locale, phrase, shuffleWithSeed, + filter-derived keys (tags[], meals[], ingredients[], source[], foodProposalPlan[] ...) | none | List<SearchRecipeCatalogItemDTO> | Food proposal (meal-plan proposal) search | `_SearchApi._SearchApi.getFoodProposal(SearchFoodRequest request)` | high |
| ✅ | GET | `~/search/food/user/{userId}` | page, limit, accessType[], hasFilters, locale, phrase, type, + filter-derived keys | none | List<SearchFoodItemDTO> | Search within user's own foods | `_SearchApi._SearchApi.getSearchUserFood(String userId, SearchFoodRequest request)` | high |
| ✅ | GET | `~/search/food/user/{userId}/last-used` | page, limit, hasFilters, locale, type, + filter-derived keys (tags[], meals[], ingredients[], source[], foodProposalPlan[], min*/max*) when set | none | List<SearchFoodItemDTO> | User's own (custom) foods last used | `_SearchApi._SearchApi.getUserLastUsedFoods(String userId, SearchFoodRequest request)` | high |
| ✅ | GET | `~/search/food/{userId}/last-used` | SearchFoodRequest.toJson() (null-omitted). Only caller (GetSearchLastUsedFoodsUseCase 0xda5a34, PlannerRepositoryImpl 0xc83674) sets just `meal` (MealType string) | none | List<SearchFoodItemDTO> (top-level JSON array) | Recently used foods for a meal (search host) | `_SearchApi._SearchApi.getLastUsedFoods(String userId, SearchFoodRequest request)` | high |
| ✅ | GET | `~/search/new/food` | page, limit, phrase, locale, hasFilters, accessType[] (e.g. FREE,PREMIUM), containNutritions, excludedProductCategories[], generic, independentDish, searchOnlyInOneLocale, targetEnergy, targetProtein, targetFat, targetCarbohydrate, type, + filter-derived keys (minEnergy..maxCookingTime, tags[], meals[], ingredients[], source[], foodProposalPlan[]) | none | List<SearchFoodItemDTO> | Main food/product search | `_SearchApi._SearchApi.getSearchFood(SearchFoodRequest request)` | high |
|  | GET | `~/search/recipe` | page, limit, ingredients[], tags[], meals[], source[], foodProposalPlan[], min/max* ranges (only filter-derived keys; no named args) | none | List<SearchFoodItemDTO> | Search recipes by ingredients | `_SearchApi._SearchApi.getFoodsByIngredients(SearchFoodRequest request)` | high |

## text-ai

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/text-ai/predict` |  | JSON (default Dio application/json) TextAiPredictRequest {"text": String, "locale": String} | TextAiPredictedDTO {prediction_id:String? (default ""), ingredients:[PredictedIngredientDTO]} | Text AI: describe a meal in free text, get predicted ingredients | `_AiEstimationPredictApi._AiEstimationPredictApi.predictTextAi(TextAiPredictRequest request)` | high |
|  | POST | `~/text-ai/predict/clarify/text` |  | JSON TextAiPredictWithUserFeedbackRequest {"prediction_id": String, "locale": String, "previous_ingredients": [PredictedIngredientDTO JSON objects - real array, not string], "user_feedback": String, "previous_input_text": String} | TextAiPredictedDTO | Text AI clarification with user feedback on previous prediction | `_AiEstimationPredictApi._AiEstimationPredictApi.predictTextAiWithUserFeedback(TextAiPredictWithUserFeedbackRequest request)` | high |
|  | POST | `~/text-ai/predict/completed` |  | AddTextAiSuccessfullyRequest {prediction_id, used_items:[{foodId:int, name, weight:double, type:FoodType string, energy, protein, fat, carbohydrate}], modified_ingredients, modified_weights} | void | Report that a text-AI prediction was accepted/added to diary. AddTextAiSuccessfullyUseCase. | `_ApiClient.addTextAiSuccessfully` | high |
|  | POST | `~/text-ai/predict/save-review` |  | AddTextAiReviewRequest {prediction_id, type:'rating'\|'comment', rating?, comment?} | void | Review/feedback for text-AI prediction. AddTextAiReviewUseCase. | `_ApiClient.addTextAiReview` | high |
|  | GET | `~/text-ai/predict/user-info` |  | none; header X-Fitatu-Test-Group (optional) | AiInfoDTO | Text-AI quota/limits. AIEstimationApiSourceImpl.getTextAiInfo. | `_ApiClient.getTextAiInfo` | high |

## token

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | POST | `~/token/refresh` |  | JSON RefreshUserTokenRequest: {"refresh_token": <stored refreshToken>} | AuthTokens {token: String (JWT), refreshToken: String? (fromJson also accepts "refresh_token"), userDemo: String? (a JSON string decoded into LoginDemoUserDto {password})} | Refresh the bearer token. Called from DioProxyHttpClientAdapter on HTTP 401, and from UserRepositoryImpl when the user's roles or featureRoles change. New tokens are saved locally. Checked live (2026-10-13): 200 {token, refresh_token}. The refresh token is a 128-character opaque string, not a JWT, and is not rotated: the reply carries the same one, it keeps working for later refreshes, and earlier bearer tokens stay valid until their exp (bearer lifetime exp - iat = 3600 s). {"refreshToken": ...} or {} answer 401 "Missing JWT Refresh Token", an unknown token 401 "JWT Refresh Token Not Found" (the messages of Symfony's gesdinet/jwt-refresh-token-bundle), no API-Key/API-Secret 403; no redirects. On user routes a bearer that is not valid gets 401 "Invalid JWT Token" and a missing one 401 "JWT Token not found", without WWW-Authenticate. | `_UnauthorizedClient (unauthorized Dio)._UnauthorizedClient.refreshAuthTokens` | high |

## upgrade-application

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/upgrade-application/{os}/{appVersion}` |  | none | AppUpdateDTO {newestApplicationVersion, upgradeApplication} | Check for a forced or optional app update. Checked live (2026-10-14): `WEB/4.13.1` answers {"newestApplicationVersion": "4.13.1", "upgradeApplication": 0}, `ANDROID/4.17.1` newest 4.17.1; without the bearer token it answers 401 "JWT Token not found", despite the unauthorized client. `fitatu-api doctor` asks it for the version the library sends. | `_UnauthorizedClient._UnauthorizedClient.getAppUpdateInfo` | high |

## user

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/user/resolutions` | year, previousYear | none. year: int? (Dart param 1). previousYear is a literal key in the query map, but every caller passes null (the constant was folded to null), so removeWhere always drops it. | HttpResponse<List<ResolutionDTO>>. Body is a JSON array of ResolutionDTO {id, name, translationTag, createdAt, userValue?}. The caller also reads the response header RESOLUTION-YEAR (first value, used as an int) to learn which year the server resolved. | Fetch the user's New Year resolutions for a year (defaults to the server-chosen year when year is null). | `_ResolutionsApi.getUserResolutions` | high |
|  | PUT | `~/user/resolutions` | year | JSON UserResolutionsRequest {resolutions: List<{id, value}>, newResolutions: List (custom user-entered resolutions; element shape not confirmed, likely strings)}. year is a required int (Dart param 2, boxed via BoxInt64). | JSON array of ResolutionDTO | Replace the user's resolutions for the given year. | `_ResolutionsApi.updateUserResolutions` | high |

## users

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/users` |  | JSON SignUpByEmailRequest: {email, password, locale, searchLocale, settings: ProfileSetupRequest{userSettings,userDietSettings,weekMealSchema}, acceptTerms, sensitiveAccepted, marketingAccepted, acceptAgeRequirements} | RegisteredUserInfoDTO {id, email}. No tokens are returned; the app logs in separately (inferred). | Email registration. SignUpUseCase also saves the chosen locale as the local storage locale, which drives the '~' host. | `_UnauthorizedClient._UnauthorizedClient.signUpByEmail` | high |
|  | DELETE | `~/users/{userId}` |  | none | HttpResponse<void> (body not parsed; status code inspected) | Delete user account. AuthApiSourceImpl.deleteAccount: if status==204 -> LogoutReason.deleteAccount else Right(null). Used via AuthGrommet. | `_ApiClient.deleteAccount` | high |
| ✅ | GET | `~/users/{userId}` |  | none | UserInfoDTO {id, username, nickname?, email, sex(int), locale, searchLocale, storageLocale, createdAt, roles[], searchUrls[], demo, marketingAccepted, enabled, matchingProcess?, systemInfo?, systemVersion?, appVersion?, accessControl[]} | Fetch the logged-in user's profile/account (roles, locales, demo flag, premium matching process). Used by UserApiSourceImpl.getUserInfo -> GetUserInfoUseCase, analytics, notification repo, priority banner. | `_ApiClient.getUserInfo` | high |
| ✅ | PATCH | `~/users/{userId}` |  | UpdateUserRequest (all keys omitted when null): {nickname?, locale?, searchLocale?, marketingAccepted?, appVersion?, systemInfo?, systemVersion?} | UserInfoDTO | Partial update of user profile/system info. UserApiSourceImpl.updateUserInfo <- UpdateUserUseCase, UpdateUserSystemInfoUseCase; afterwards app clears recipe/product cache and cached ~/users/{id}/subscriptions. | `_ApiClient.updateUser` | high |

## users/…/action

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/users/action/reset-password/send-email` |  | JSON {"email": email} | void | Send a forgot-password email. | `_UnauthorizedClient._UnauthorizedClient.sendResetPasswordEmail` | high |
|  | PUT | `~/users/{userId}/action/resend-activation-mail` |  | none | void | Resend account activation e-mail. UserApiSourceImpl.resendActivationMail <- ResendActivationMailUseCase. | `_ApiClient.resendActivationMail` | high |

## users/…/activities

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/users/{userId}/activities/{activityId}` |  | none. activityId is an int. | ActivityDetailsDTO {id?, activityId, name, description?, type, intensity, advanced, simple, lastUsedMeasureType, simpleMeasures?, advancedMeasures?, formulaParams{sex, bmr}} | Details of one activity including the measures form and the energy formula params, personalised for the user. | `_ActivitiesApi.getActivity` | high |

## users/…/demo

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | PATCH | `~/users/demo` |  | ConvertDemoUserRequest {acceptAgeRequirements, acceptTerms, email, marketingAccepted, newPassword, sensitiveAccepted} | void | Convert demo user into a regular email/password account. UserApiSourceImpl.convertDemoUser <- ConvertDemoUserUseCase. | `_ApiClient.convertDemoUser` | high |

## users/…/facebook-demo-registration

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/users/{userId}/facebook-demo-registration` |  | ConvertDemoUserUsingFacebookRequest {facebookUserAccessToken, facebookUserId, locale, partnerId:'07b92206-189c-11e8-81d0-0242ac120007' (hard-coded), tokenType:'LIMITED'\|'FULL', acceptTerms, sensitiveAccepted, marketingAccepted} | AuthTokens {token, refreshToken? (also accepts 'refresh_token'), userDemo? (JSON string -> {password})} | Convert a demo/guest account into a Facebook-linked account; returns fresh JWT tokens. AuthApiSourceImpl.convertDemoUserUsingFacebook <- ConvertDemoUserUsingAppUseCase. | `_ApiClient.convertDemoUserUsingFacebook` | high |

## users/…/favorite

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | PUT | `~/users/{userId}/favorite/food/{foodType}/{foodId}` |  | FavoriteFoodRequest {markAsFavorite: bool} | dynamic (response.data returned, ignored) | Mark/unmark product or recipe as favourite. FavoriteFoodApiSourceImpl.updateFavoriteFood <- UpdateFavoriteFoodUseCase (product card, recipe card, recipe catalogs); then CacheManager.cleanRecipeAndProductRelated. | `_ApiClient.updateFavoriteFood` | high |
| ✅ | GET | `~/users/{userId}/favorite/foods` | page, limit, generic, hasFilters, locale, phrase, type, + filter-derived keys | none | List<SearchFoodCatalogItemDTO> | User favorite foods (served via search Dio/host) | `_SearchApi._SearchApi.getUserFavoriteFoods(String userId, SearchFoodRequest request)` | high |

## users/…/habits

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/users/{userId}/habits` |  | none | List<UserHabitDTO {completed, enrolledDate, habit{id,name,translationKey,length,premium}, habitItems[{dayNumber,date}]}> | The user's enrolled habits with their check-in days | `_HabitApi.getUserHabits` | high |
|  | PUT | `~/users/{userId}/habits/{habitId}` |  | JSON object UpdateHabitRequest {habitItems: [{dayNumber: int, date: yyyy-MM-dd}], completed: bool, enrolledDate: yyyy-MM-dd} | UserHabitDTO {completed: bool, enrolledDate: String, habit: HabitDTO, habitItems: List<HabitCompletedDayDTO>} | Enroll in, restart, finish or check in a habit (the whole state is upserted) | `_HabitApi.updateHabit` | high |

## users/…/measurements

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/users/{userId}/measurements/chart/weight` | fromDate (optional, yyyy-MM-dd), toDate (optional, yyyy-MM-dd) | none (WeightMeasurementsChartRequest is sent as @Queries. Its toJson is code-folded onto _$DifferencesSummaryRequestToJson, so it has the same keys) | WeightMeasurementsChartDTO {weights: Map<String,double> (EmptyListToEmptyMapConverter: [] means {}; keys inferred as dates), targets: Map<String,double>, weightUnit: String} | Weight chart data plus targets over a date range | `_BodyMeasurementsApi.getWeightMeasurementsChart` | high |
| ✅ | GET | `~/users/{userId}/measurements/size/{bodyPart}` | fromDate (optional, yyyy-MM-dd), toDate (optional, yyyy-MM-dd), limit (always sent, string, app default "41"), page (always sent, int.toString()) | none (BodyPartMeasurementsRequest sent as @Queries) | List<BodyPartMeasurementDTO {date: String parsed with DateTimeExt.parseUtc, value: num\|String via SafeDoubleConverter}> | Paged history of one body-part size measurement | `_BodyMeasurementsApi.getBodyPartMeasurements` | high |
| ✅ | GET | `~/users/{userId}/measurements/summary/size` | fromDate (optional, yyyy-MM-dd), toDate (optional, yyyy-MM-dd) | none (DifferencesSummaryRequest is sent as @Queries: its toJson is addAll'ed into queryParameters, which goes to compose() arg x6; data x5=Null) | DifferencesSummaryDTO {neck, chest, waist, stomach, hips, thigh, calf, biceps, fatPercentage}; each value is BodyPartDifferenceSummaryDTO {startValue?, endValue?, difference?} | Body-size differences summary (start/end/difference per body part) over a date range | `_BodyMeasurementsApi.getDifferencesSummary` | high |
| ✅ | GET | `~/users/{userId}/measurements/summary/weight` | fromDate (optional, yyyy-MM-dd), toDate (optional, yyyy-MM-dd), limit (always, string, default "41"), page (always, int as string) | none (BodyPartMeasurementsRequest sent as @Queries) | List<WeightMeasurementDTO {date: String, value: double (SafeDouble), difference?: double, direction: int enum UserWeightGoalType (1=gain, -1=reduction, 0=maintenance)}> | Paged weight-measurement history with deltas and trend direction | `_BodyMeasurementsApi.getWeightMeasurements` | high |
| ✅ | DELETE | `~/users/{userId}/measurements/{date}` |  | none | void | Delete the measurement entry for one day | `_BodyMeasurementsApi.deleteMeasurement` | high |
| ✅ | GET | `~/users/{userId}/measurements/{date}` |  | none | MeasurementDTO {weight?, neck?, chest?, waist?, stomach?, hips?, thigh?, calf?, biceps?, fatPercentage? (all num\|String via SafeDoubleConverter, nullable), sizeUnit?: String, weightUnit?: String} | Fetch the measurement entry for one day (to prefill the edit form). Recorded: 404 when the day has no entry. | `_BodyMeasurementsApi.getMeasurementByDate` | high |
| ✅ | PUT | `~/users/{userId}/measurements/{date}` |  | JSON object AddMeasurementRequest: {date, weight?, neck?, chest?, waist?, stomach?, hips?, thigh?, calf?, biceps?, fatPercentage, weightUnit, sizeUnit}. Measurement values are sent as STRINGS (double.toString()). Null measurements are omitted. | void (any 2xx). Recorded: 200 with the saved entry as JSON. | Add or overwrite the body measurement (weight, sizes, fat %) for a given day | `_BodyMeasurementsApi.updateBodyMeasurement` | high |

## users/…/meta

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | GET | `~/users/{userId}/meta` |  | none | UserMetaDTO {newYearResolutions2026: String? (a JSON-encoded string that decodes to {fb, ig, tt, hb, sh, kn, yt} booleans)} | Read the per-user meta key/value bag (currently only the 2026 resolutions campaign flags). | `_UserMetaApi.getUserMeta` | high |
|  | PATCH | `~/users/{userId}/meta` |  | JSON UserMetaDTO {newYearResolutions2026: String}. The value is the jsonEncode of {fb, ig, tt, hb, sh, kn, yt}, a JSON string inside JSON. The key is omitted when null. | UserMetaDTO | Update user meta flags (2026 resolutions campaign). | `_UserMetaApi.patchUserMeta` | high |

## users/…/notification-tokens

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | DELETE | `~/users/{userId}/notification-tokens` |  | UserNotificationTokenRequest {notificationToken} (JSON body on DELETE) | void | Unregister push token on logout. AuthApiSourceImpl.deauthorizeMessagingToken <- AuthRepositoryImpl (skipped if token null). | `_ApiClient.deleteUserNotificationToken` | high |
|  | POST | `~/users/{userId}/notification-tokens` |  | UserNotificationTokenRequest {notificationToken: String (FCM token)} | void | Register the device push (Firebase messaging) token. UserApiSourceImpl.updateUserMessagingToken <- SynchronizeUserMessagingTokenUseCase. | `_ApiClient.postUserNotificationToken` | high |

## users/…/settings

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | PATCH | `~/users/{userId}/settings/premium` |  | PremiumSettingsRequest {matchingProcess: false} (literal false in this build) | void | Finish/reset premium onboarding 'matching process' flag. UserApiSourceImpl.updatePremiumSettings <- UpdatePremiumSettingsUseCase. | `_ApiClient.updatePremiumSetting` | high |

## users/…/settings-new

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | PATCH | `~/users/{userId}/settings-new` |  | JSON SettingsPartialUpdateRequest {userId: int (always), updatedAt: ISO8601 string or null (always present), userDietSettings?, userSettings?, waterSettings?, plannerSettings?, weekMealSchema?, foodProposalSettings?}. Each optional section is omitted when null. | SettingsDTO (full updated settings) | Partial write of settings: goals, macro targets, meal schema, water unit, planner appearance, food proposal plan, intermittent fasting, body data. | `_SettingsApi.updateSettingsPartial` | high |
| ✅ | GET | `~/users/{userId}/settings-new/{date}` |  | none. date is a String formatted yyyy-MM-dd (DateFormat in SettingsApiSourceImpl). | SettingsDTO {userId, date, userSettings, calculatedValues, weekMealSchema, foodProposalSettings?, plannerSettings, userDietSettings, waterSettings, weekMealRecommendedEnergy, updatedAt?} | Effective user settings for a given day: body data, goals, energy formula outputs, macro targets, meal schema, water, planner. Recorded: a GET without the date segment answers 405. | `_SettingsApi.getSettings` | high |

## users/…/subscriptions

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
| ✅ | GET | `~/users/{userId}/subscriptions` |  | none | UserSubscriptionHistoryDTO {subscriptions: List<UserSubscriptionDTO{id, startDate, endDate, planName, planGroupType, provider, providerId, duration, description?}>} | History of the user's subscriptions. Checked live (2026-10-14): each also has planGroupName, amount, currency and lastOriginalTransactionId; `user_settings.get_premium` keeps only whether one runs and its end date. | `_UserSubscriptionApi.getUserSubscriptionHistory` | high |

## voice-ai

| | Method | Path | Query | Body | Response | Purpose | Dart method | Conf. |
|---|---|---|---|---|---|---|---|---|
|  | POST | `~/voice-ai/predict/clarify/voice` |  | multipart/form-data. Parts in order: "audio" (audio/m4a, filename=audioName); "prediction_id"; "locale"; "previous_ingredients" = JSON string (jsonEncode of List<PredictedIngredientDTO.toJson()>); "previous_transcription". All required strings. (Empty query map gets removeWhere(null) - no query params.) | VoiceAiPredictedWithUserFeedbackDTO {prediction_id:String? (default ""), feedback_id:String?, ingredients:[PredictedIngredientDTO], valid_input:bool?, transcription:String?} | Voice AI clarification: send a follow-up voice correction plus previous result | `_AiEstimationPredictApi._AiEstimationPredictApi.predictVoiceAiWithUserFeedback(List<MultipartFile> files, String predictionId, String locale, String previousIngredients, String previousTranscription)` | high |
|  | POST | `~/voice-ai/predict/completed` |  | AddVoiceAiSuccessfullyRequest {prediction_id, used_items:[same item shape as text-ai], modified_ingredients, modified_weights} | void | Report accepted voice-AI prediction. AddVoiceAiSuccessfullyUseCase. | `_ApiClient.addVoiceAiSuccessfully` | high |
|  | POST | `~/voice-ai/predict/save-review` |  | AddVoiceAiReviewRequest {prediction_id, type:'rating'\|'comment', rating?, comment?} | void | Thumbs/rating/comment feedback for a voice-AI prediction. AddVoiceAiReviewUseCase. | `_ApiClient.addVoiceAiReview` | high |
|  | GET | `~/voice-ai/predict/user-info` |  | none; header X-Fitatu-Test-Group (optional) | AiInfoDTO | Voice-AI quota/limits. AIEstimationApiSourceImpl.getVoiceAiInfo. | `_ApiClient.getVoiceAiInfo` | high |
|  | POST | `~/voice-ai/predict/voice` |  | multipart/form-data. Parts: "audio" file part(s) (app sends one: MultipartFile.fromBytes(audioBytes, filename: request.audioName, contentType: audio/m4a)); text field "locale". | VoiceAiPredictedDTO {prediction_id:String? (default ""), transcription:String, ingredients:[PredictedIngredientDTO]} | Voice AI: upload an m4a voice recording describing a meal, get transcription + predicted ingredients | `_AiEstimationPredictApi._AiEstimationPredictApi.predictVoiceAi(List<MultipartFile> files, String locale)` | high |

## Appendix — DTO JSON keys

| DTO | JSON keys | Notes |
|---|---|---|
| `AcceptConsentRequest` | version |  |
| `ActivitiesGroupDTO / SearchActivityItemDTO` | letter, activities, activityId, name, type, intensity, energy, measuresDescription, measures, uuid | type is ActivityType int: import=0, strength=1, cardio=2, stretching=3. intensity is ActivityIntensityType int: 1 to 4 (low, mid, high, higher). measures is a List<ActivityMeasureDTO>. uuid is String?. |
| `ActivityDetailsDTO / ActivityFormulaParamDTO` | id, activityId, name, description, type, intensity, advanced, simple, lastUsedMeasureType, simpleMeasures, advancedMeasures, formulaParams, sex, bmr | lastUsedMeasureType is a string, "simple" or "advanced" (_ActivityMainMeasureTypeConverter). formulaParams = {sex: Sex int, bmr: num}. |
| `ActivityMeasureDTO` | measureId, name, short, type, scale, selected, value, defaultValue | type is PlannerActivityMeasureType: TIME, SERIES, REPEATS, LOAD, DISTANCE. The fromJson factory also accepts the alias keys measureName, measureShort and measureType and copies them to name, short and type when non-null. |
| `ActivityPlanDTO (response activity)` | uuid, date, definedActivityType, planDayActivityType, measureType, energy, activityTime, daily, distance, steps, activityId, originId, energyTotal, activityName, activityDescription, activityCustom, activitySource, activityDescriptionEnergy, activityStartedAt, activityStoppedAt, simpleMeasures, advancedMeasures, deletedAt | definedActivityType: int enum (ActivityType). planDayActivityType: int 1 api / 2 custom / 3 import. measureType: int? (ActivityMainMeasureType). distance may arrive as a String; ActivityPlanDTO.fromJson double.parse()s it. activityCustom: {id, name}. activityStartedAt/StoppedAt: {date: String, timezone: String ('UTC' default)} via ActivityDateConverter. simpleMeasures/advancedMeasures: List<ActivityMeasureDTO> {measureId, name, short, type, scale, selected, value, defaultValue}. Legacy keys measureName/measureShort/measureType are remapped to name/short/type. Recorded: the server also sends id, planDayId and activityType, and a day sync that sends an activity back in this read shape is rejected field by field (202 with errorMessage); an activity is deleted by sending it as PlannerUpdateApiActivityRequest with deletedAt. |
| `AddMeasurementRequest (PUT body)` | date, weight, neck, chest, waist, stomach, hips, thigh, calf, biceps, fatPercentage, weightUnit, sizeUnit | date uses DateConverter (yyyy-MM-dd). weight through biceps are nullable doubles serialized via StringToDoubleConverter, which calls double.toString(), so the JSON values are STRINGS such as "72.5"; they are omitted when null. fatPercentage is a non-null double, always sent as a string. weightUnit and sizeUnit are always present, set to BodyMeasureUnit API keys: weight unit KG\|LB\|ST_LB, size unit CM\|IN\|FT_IN. They come from form.weight.unit and form.<size>.unit (update_body_measurement_use_case.dart @0xd92fd8, body_measure_unit.dart BodyMeasureUnit.fromKey). Values are converted to the base system by convertValue() before sending. add_measurement_request.dart @0xd93728 |
| `AddPhotoAiEstimationRequest` | predictionId, name, descriptionAi, items | items[{itemId:int, measureId:int, measureQuantity:double}] (camelCase, unlike other AI endpoints). |
| `AddPhotoAiEstimationSuccessfullyRequest` | prediction_id, feedback_id, used_items, modified_ingredients, modified_weights | used_items[{foodId:int, name, weight, type, energy, protein, fat, carbohydrate, is_alternative}]. |
| `AddProductBarcodeRequest` | barcode | features/add_product_barcode/data/request/add_product_barcode_request.dart |
| `AddProductPropertyProposalRequest` | propertyName, propertyValue | propertyName in {rawIngredients, category, undefined}; propertyValue passed through (String inferred from ProposalDTO.propertyValue) |
| `AddReviewContentRequest` | content, itemId | itemId int. |
| `AddReviewRateRequest` | rate | int. |
| `AddTextAiReviewRequest / AddVoiceAiReviewRequest` | prediction_id, type, rating, comment | type 'rating'\|'comment'; rating/comment omitted when null. |
| `AddTextAiSuccessfullyRequest / AddVoiceAiSuccessfullyRequest` | prediction_id, used_items, modified_ingredients, modified_weights | used_items[{foodId:int, name, weight:double, type:FoodType string, energy, protein, fat, carbohydrate}]. |
| `AdditionalBillingDataRequest` | developerPayload | developerPayload = userId string. |
| `AiInfoDTO` | limit_prediction, counter_prediction, model_release_version | snake_case; ints via SafeIntConverter, model_release_version double?. |
| `ApiEndpoints / ApiEndpoint (local + Remote Config SEARCH_ENDPOINTS)` | baseEndpoint, searchEndpoint, url, version | Remote Config 'SEARCH_ENDPOINTS' is a JSON list of {url, version}. Default versions are 'initial' (base) and 'search-v2' (search); version appears informational only. |
| `AppUpdateDTO` | newestApplicationVersion, upgradeApplication | Response of GET ~/upgrade-application/{os}/{version}. |
| `AuthQrDto (scanned QR, local only)` | username, jwt | QR login does not call the API. It stores AuthTokens(token=jwt) with no refreshToken. |
| `AuthTokens` | token, refreshToken, userDemo | AuthTokens.fromJson also accepts 'refresh_token'. userDemo is a JSON-encoded string decoded into LoginDemoUserDto {password}. |
| `AuthTokens (response of login, login-facebook, login-demo, token/refresh; also local storage format)` | token, refreshToken, userDemo | fromJson: if 'refreshToken' is missing it copies 'refresh_token' into 'refreshToken' (@0xbdff58). 'token' is a required String. 'userDemo' is a String that is jsonDecoded into LoginDemoUserDto {password}. Stored as JSON in Hive box 'fitatu_db_box.api_box', key 'auth_tokens_key', and mirrored to SecureStorage under the same key. |
| `BillingCallbackDTO` | ok, data, errorCode | data -> BillingCallbackProductDTO {product: String}; errorCode nullable enum string: UNKNOWN, UNSUPPORTED_PURCHASE, NON_UNIQUE_PAYMENT, APP_STORE_RECEIPT_CANNOT_BE_EMPTY, PURCHASE_SUBJECT_USER_WAS_NOT_FOUND, USER_ID_IN_PURCHASE_NOT_FOUND, PURCHASE_USER_ID_IS_DIFFERENT_THAN_SUBJECT_USER, ORDER_USER_IS_DIFFERENT_THAN_SUBJECT_USER, INVALID_PRODUCT, UNRECOGNISED_INCOMING_PURCHASE, INVALID_OBFUSCATED_ACCOUNT_ID, UNSUPPORTED_TRANSACTION, INVALID_JSON, INVALID_PURCHASE_USER, INVALID_RECEIPT, DUPLICATED_PROVIDER_ID, ORDER_BY_INTERNAL_ID_NOT_FOUND, INVALID_SIGNATURE, NON_MATCHING_PURCHASE_AMOUNT, NON_MATCHING_PURCHASE_CURRENCY, NON_EXISTING_ORDER, INVALID_BUNDLE_ID, INVALID_PACKAGE_NAME, UNPROCESSABLE_DATA. |
| `BodyPartDifferenceSummaryDTO` | startValue, endValue, difference | All are nullable doubles (SafeDoubleConverter via _$JsonConverterFromJson) |
| `BodyPartMeasurementDTO (response item)` | date, value | date is a String parsed with DateTimeExt.parseUtc. value is required and parsed with SafeDoubleConverter |
| `BodyPartMeasurementsRequest (query)` | fromDate, toDate, limit, page | fromDate and toDate are omitted if null (yyyy-MM-dd). limit and page are always present. limit is a String: the source uses limit?.toString() ?? "41". page is an int converted to string. body_part_measurements_request.dart @0xd8fbec. Source closures @0xd8f6c0 and @0xd907bc |
| `BtwModel / CaloriesSmallModel (widget UI models)` | isLoggedIn, isPremium, caloriesLeft, caloriesPercentage, proteinsPercentage, fatsPercentage, carbohydratesPercentage | CaloriesSmallModel has only isLoggedIn, isPremium, caloriesLeft, caloriesPercentage. These are the Glance widget state models (wc/a.java, wc/d.java), built from the DTOs above. They are not wire formats. |
| `CalculatedValuesDTO / BmrConstantDto` | activityEnergy, calculatedEnergy, calculatedTempo, deficit, BMR, TMR, PAL, BMRConstant, formulaCoefficient, formulaElement | These are the energy formula outputs: BMR, TMR (total metabolic rate), PAL, kcal deficit and tempo. activityEnergy and calculatedEnergy are int; calculatedTempo is double. BMRConstant = {formulaCoefficient, formulaElement}. The keys are uppercase BMR, TMR and PAL. |
| `ConnectedAppsAuthorizationDto` | authorized | bool |
| `ConnectedAppsConnectionStatusDto (local storage only, not in this API)` | adidas, adidasInstalled, healthImport, healthExport, garminConnect, huaweiHealth, nikePlus, nikePlusInstalled, samsungHealth, samsungHealthInstalled, polarFlow, strava, zeppLife, zeppLifeInstalled, homeWorkout, zepp, hevy, ksFit, whoop, lastHealthTraining | bool? flags + lastHealthTraining String; used only by connected_apps_local_storage_source_impl |
| `ConsentAggregateDTO / ConsentStatusItemDTO / ConsentVersionInfoDTO` | consents, type, status, accepted, latest, version | status is a ConsentStatus enum: NONE, REVOKED, OUTDATED, ACCEPTED. |
| `ConsentVersionTextDTO / ConsentContentDTO` | type, version, content, title, text |  |
| `ConvertDemoUserRequest` | acceptAgeRequirements, acceptTerms, email, marketingAccepted, newPassword, sensitiveAccepted | All always sent. |
| `ConvertDemoUserUsingFacebookRequest` | facebookUserAccessToken, facebookUserId, locale, partnerId, tokenType, acceptTerms, sensitiveAccepted, marketingAccepted | partnerId constant '07b92206-189c-11e8-81d0-0242ac120007'; tokenType 'LIMITED'\|'FULL'. |
| `DataExportRequest (query)` | format, fromDate, toDate | format: 'xls' \| 'pdf' \| 'txt' \| 'csv'. Dates: yyyy-MM-dd. |
| `DietPlanDTO / MealDTO (response)` | breakfast, second_breakfast, lunch, dinner, snack, supper, items | Each meal is a nullable Map {items:[MealItemDTO]}. |
| `DifferencesSummaryDTO (response)` | neck, chest, waist, stomach, hips, thigh, calf, biceps, fatPercentage | Each value is a Map parsed as BodyPartDifferenceSummaryDTO |
| `DifferencesSummaryRequest / WeightMeasurementsChartRequest (query)` | fromDate, toDate | Both keys are omitted when null (includeIfNull false). Values are Date.yyyyMMdd() strings, i.e. "yyyy-MM-dd". differences_summary_request.dart _$DifferencesSummaryRequestToJson @0xd6e038 |
| `FastingDTO (fasting type, response item)` | id, symbol, currentlyInUse, rating, reviewsPerLocale | id is parsed by SafeInt. symbol is the FastingType enum: "14_10_hours", "16_08_hours", "18_06_hours", "20_04_hours" or "other" (also the unknownValue). currentlyInUse is a SafeDouble. rating is a nullable double. reviewsPerLocale is a List of ReviewsPerLocaleDTO {count: int, locale: String} |
| `FastingEatingPeriodDTO` | date, from, to | date: String. from and to: time Strings parsed with Time.parse (format not verified, likely HH:mm) |
| `FastingPeriodDTO (response item)` | id, date, startDate, endDate, default, deleted | id: String. date, startDate and endDate are Strings parsed with DateTime.parse. default and deleted: bool |
| `FastingPeriodRequest (POST body item)` | id, date, startDate, endDate, default, deleted, isCreatedDynamically | All keys are always present (map literal; nulls included). id: String? (uuid-like, copied from the entity). date: DateConverter, yyyy-MM-dd. startDate and endDate: DateTimeConverter, "yyyy-MM-dd HH:mm:ss" formatted from the DateTime as-is (local time, no toUtc), or null. default and deleted: bool. isCreatedDynamically: bool, which the app always sets to false (update_fasting_periods_use_case.dart). fasting_period_request.dart _$FastingPeriodRequestToJson |
| `FastingSummaryDTO (response item)` | date, eatingPeriods, expectedEatingPeriods | date: String (parsed). Both lists contain FastingEatingPeriodDTO |
| `FavoriteFoodRequest` | markAsFavorite | bool. |
| `FillIncompleteProductPropertiesDTO / ProductUpdatedDTO / ProductCreatedDTO` | id | id via SafeIntConverter |
| `FillIncompleteProductPropertiesRequest` | energy, fat, carbohydrate, protein, saturatedFat, sugars, fiber, salt | First four are non-null doubles; last four nullable, written even when null (map literal). |
| `FoodMeasureDTO` | measureId, measureName, measureQuantity, measureWeight, measureEnergy | features/food/data/dto/food_measure_dto.dart; nested 'measure' in search DTOs |
| `FoodProposalSettingsDTO (freezed union)` | planId, planName, planLocale, planRecipeTags, updatedAt, runtimeType | Full variant: {planId, planName, planLocale, planRecipeTags: [{name, category, translation?}], updatedAt?, runtimeType: "full"}. Empty variant: {planId, runtimeType: "empty"}. When writing, the app includes the runtimeType key. When reading, the factory forces runtimeType = "full". |
| `FoodShareItemDTO` | meal, measureId, measureQuantity, type, itemId, measureWeight, ingredientsServing, customItem | meal: breakfast\|second_breakfast\|lunch\|dinner\|snack\|supper; type FoodType string; itemId/measureWeight/ingredientsServing/customItem nullable (omitted). customItem {name, energy, energyCalories, protein, fat, carbohydrate}. |
| `FoodShareRequest` | userId, mealsDate, items | userId int. |
| `FoodShareResponseDTO` | hash |  |
| `FreeAiScansLimitsConfig (Remote Config AB_TEST_FREE_AI_SCANS_LIMITS)` | GROUP_NAME, FREE_SCAN_COUNT, SHOW_BANNERS_INFO | GROUP_NAME becomes the X-Fitatu-Test-Group header value (default 'BASE'). |
| `GameActiveDaysRequest (query)` | from, to | Both are Date values formatted yyyy-MM-dd (DateConverter). |
| `GameActiveDaysResponseDTO / GameActiveDayDTO` | days, date, type | days[]: {date: String, type: GameActiveDayTypeDTO with values QUEST, SHIELD, UNKNOWN} |
| `GameQuestDTO (fromJson)` | id, type, status, endDate, startDate, actionList, rewardCollected, rewardTypes | id: String. rewardCollected: bool. rewardTypes: List<GameRewardType> with values BASE, BONUS, UNKNOWN. |
| `GameQuestDTO (toJson for PUT /game/quests)` | id, type, status, endDate, startDate, actionList | features/gamification/data/dto/game_state_dto.dart _$GameQuestDTOToJson @0xd96438. type maps through Map<GameQuestType,String> {DAILY, UNKNOWN}. status maps through Map<GameStatusType,String> {ACTIVE, INACTIVE, COMPLETED, PROTECTED, UNKNOWN}. endDate and startDate are DateTime values serialised with a virtual call (inferred toIso8601String; fromJson uses DateTime.parse). actionList = [{type}] with GameActionType values {active_day, unknown} (_$GameActionToJson in domain/entity/game_state.dart). rewardCollected and rewardTypes are read only and are not sent. |
| `GameRewardBoxRulesDTO` | rules, daily, type, jewels, streakDay, streakFrom, repeatEvery | rules.daily[]: type uses GameRewardBoxRuleTypeDTO {BASE, MILESTONE, RECURRING, UNKNOWN}. streakDay, streakFrom and repeatEvery are int? (SafeInt converter). |
| `GameRewardBoxesToCollectDTO` | boxes, type, jewels, createdAt | boxes[] item class is GameGameRewardBoxToCollectDTO; createdAt is a String. |
| `GameRewardsCollectedDTO` | rewards, type, jewels | rewards[]: {type: GameRewardType (BASE, BONUS, UNKNOWN), jewels: num} |
| `GameStateDTO` | content | content: List<GameQuestDTO> |
| `GameStateRequest (query)` | from, to | Both are DateTime sent as UTC ISO8601 strings (DateTimeTimezoneConverter). |
| `GameStoreProductsDTO / GameStoreProductDTO` | content, productId, price, items, currency, amount, type, resourceType | price = {currency: GameCurrencyType, amount}. items[] = {amount, type: GameStoreProductItemType (ADD_RESOURCE, INCREASE_CAPACITY, UNKNOWN), resourceType: GameStoreProductItemResourceType (SHIELD, UNKNOWN)}. |
| `GameStorePurchaseDTO` | currencies, resources | Map keys are enum strings: currencies uses JEWELS, resources uses SHIELD. Values are GameCurrencyDTO and UserGameResourceDTO. |
| `GameStorePurchaseRequest` | productId, quantity | Request body for POST ~/game/store/purchase. |
| `GarminAuthorizationRequest` | verifier, token, tokenSecret |  |
| `GarminTokensDTO` | token, tokenSecret | Strings (OAuth1 request token/secret) |
| `HabitCompletedDayDTO` | dayNumber, date | dayNumber: int. HabitCompletedDayDTO.fromEntity always sets it to 1 (@0xdc455c). date: yyyy-MM-dd |
| `HabitDTO` | id, name, translationKey, length, premium | id: SafeInt. name and translationKey: String. length: number (int inferred). premium: bool |
| `HuaweiHealthAuthorizationRequest / StravaAuthorizationRequest` | code | identical toJson (code-folded) |
| `IntermittentFastingConfigDTO / DefaultWeekDayConfigDTO` | default, type, startDate, start, end | default = {start, end} time strings (format inferred HH:mm). type is FastingType or null: 14_10_hours, 16_08_hours, 18_06_hours, 20_04_hours, other. startDate is String?. |
| `LabelScanMergedPortionDTO` | portion_name, portion_weight | portion_name LabelScanPortionType nullable: package\|serving\|other; portion_weight double |
| `LabelScanProcessPhotoRequest / LabelScanSkipPhotoRequest (internal)` | file, fileName, sessionId, imageType | Internal; imageType LabelScanType -> path segment front\|nutrients\|ingredients |
| `LabelScanResultDTO` | session_id, codename, session_data, merged_portions, base_type | codename LabelScanStatus: PROCESSING_COMPLETED\|PROCESSING_IN_PROGRESS\|CLIENT_AWAITING_IMAGES\|ERRORS\|other; base_type LabelScanBaseType: default\|package\|portion (unknownValue -> default) |
| `LabelScanSaveCompletedSessionRequest` | session_id, front_modified_json, ingredients_modified_json, nutrients_modified_json, merged_portions_modified, modified_front, modified_nutrients, modified_ingredients, modified_merged_portions | front_modified_json {product_name, brand_name}; ingredients_modified_json {ingredients}; nutrients_modified_json {base_value, base_unit, <nutrient>:{value}}; merged_portions_modified [{portion_name, portion_weight, portion_unit (default "g")}]; modified_* bool (inferred) |
| `LabelScanSessionDataDTO` | status_front, front_extracted_json, status_ingredients, ingredients_extracted_json, status_nutrients, nutrients_extracted_json, product_category_prediction | status_* LabelScanDataStatus nullable: COMPLETED\|SKIPPED (unknownValue SKIPPED); front_extracted_json {product_name?, brand_name?}; ingredients_extracted_json {ingredients:String}; nutrients_extracted_json {base_value, base_unit, energy:{value}, protein:{value}, ...same 41 nutrient keys as photo DTO, each {value: double}}; product_category_prediction -> ProductCategoryPredictDTO (features/product) |
| `LabelScanSessionIdRequest` | session_id | body for results and skip_photo |
| `LabelScanSessionStartDTO` | session_id | String |
| `LoginDemoUserDto` | password | Carried inside AuthTokens.userDemo as a JSON string. |
| `MealItemDTO (response diet item)` | planDayDietItemId, name, foodType, measureId, measureName, measureQuantity, energy, protein, fat, carbohydrate, weight, ingredientsServing, preparationTime, cookingTime, fiber, cholesterol, saturatedFat, sugars, salt, sodium, iron, productId, brand, recipeId, mainPhoto, description, rating, deletedAt, source, foodProposalPlan, liquid, recipeAI | _$MealItemDTOFromJson. planDayDietItemId, name and measureName are required Strings. foodType is an enum string (see above). measureId uses SafeInt. measureQuantity uses SafeDouble. Nutrients are nullable doubles; preparationTime/cookingTime are nullable ints. mainPhoto: PhotoDTO {alt, path, pathThumbnail, pathBigThumbnail, pathLargeThumbnail, ai} via fromJsonSafe. rating: {rate, votes}. deletedAt: String? parsed as UTC (OptionalDateTimeUTCSerializer.tryParseUtc). source: int 1/2/3 or 'API'/'GENERATOR'/'FOOD_PROPOSAL'. liquid: bool (required). recipeAI: {id, aiModel}? |
| `MeasurementDTO (response)` | weight, neck, chest, waist, stomach, hips, thigh, calf, biceps, fatPercentage, sizeUnit, weightUnit | All numeric fields are nullable and parsed by SafeDoubleConverter, which accepts num or String. sizeUnit and weightUnit are String? keys parsed with BodyMeasureUnit.fromKey (KG, LB, ST_LB, CM, IN, FT_IN) |
| `MenuPlanDTO` | id, name, description, mainPhoto, numberOfUse, quote, quotePerson, quotePersonTitle, rating, reviewsCount, ratingCount, shortDescription, visible, descriptionTags, recipeTags, ratingDetails | id and numberOfUse: SafeInt. name: String. description, quote, quotePerson, quotePersonTitle and shortDescription: String?. mainPhoto: nullable PhotoDTO {alt, path, pathThumbnail, pathBigThumbnail, pathLargeThumbnail, ai(bool)}. rating: double?. reviewsCount and ratingCount: int?. visible: bool. descriptionTags: List<String enum>? with values better_metabolism, greater_vitality, better_organism_condition, low_caloric, cholesterol_reduction, better_mood, healthy_teeth_smooth_skin, cancer_prevention, strengthening_organism, reduction_of_adipose_tissue, lowering_blood_sugar_levels, decreased_appetite. recipeTags: List<{name: String, category: String}>. ratingDetails: nullable RatesDTO {rating: {rate: double, votes: int}, userVote: int?} |
| `MenuPlansDTO` | popular, elimination, all | Three lists of MenuPlanDTO |
| `MinPricePlanDTO` | duration, currency, amount, productId, provider, isRenewal, planType |  |
| `NoteDTO (response, after client fold)` | note, tagsIds | note: String?. tagsIds: List? mapped to NoteTag. |
| `NutritionSchemaItemDTO` | energy, carbohydratePercentage, carbohydrateWeight, fatPercentage, fatWeight, proteinPercentage, proteinWeight | Per-weekday macro and energy override. |
| `PaymentCheckDTO` | purchaseId, processed, paymentId, subscriptionId, planGroupType | purchaseId int? via SafeIntConverter; processed bool. |
| `PaymentPlanDTO` | planId, name, provider, type, bestseller, duration, id, defaultPlan, renewableOnWeb, currency, price, promoPrice, durationProration, accessGroup | provider is AppPlatform: ANDROID, IOS, other. type is PaymentPlanType: PREMIUM, PREMIUM_PLUS_AI, NO_ADS, other. durationProration = {extendedDuration, additionalDays, nextRenewal?}. accessGroup = [{code, priority}]. |
| `PaymentProductsDTO` | plans, promotions, minPrices | features/offer/data/dto/payments_products_dto.dart |
| `PhotoAiEstimationAddedDTO` | id | int id of created/updated recipes-ai item. |
| `PhotoAiEstimationDTO` | id, name, photos, recipeAI, items, userId | photos[{id,path}]; recipeAI {id, aiModel}; items[PhotoAiEstimationIngredientDTO {itemId, name, measureQuantity, nutrients, type}] where nutrients keys: energy, protein, vegetableProtein, animalProtein, fat, saturatedFat, polyunsaturatedFat, monounsaturatedFat, carbohydrate, cholesterol, fiber, sodium, salt, calcium, potassium, iron, magnesium, zinc, phosphorus, iodine, copper, selenium, vitaminA..vitaminPP, folicAcid, sugars, omega3, omega6, caffeine, glycemicIndex, proteinFatExchangers, glycemicLoad, carbohydrateExchangers. |
| `PhotoAiEstimationIngredientNutrientsDTO` | energy, protein, vegetableProtein, animalProtein, fat, saturatedFat, polyunsaturatedFat, monounsaturatedFat, carbohydrate, cholesterol, fiber, sodium, salt, calcium, potassium, iron, magnesium, zinc, phosphorus, iodine, copper, selenium, vitaminA, vitaminB1, vitaminB2, vitaminB5, vitaminB6, vitaminB7, vitaminB12, vitaminC, vitaminD, vitaminE, vitaminK, vitaminPP, folicAcid, sugars, omega3, omega6, caffeine, glycemicIndex, proteinFatExchangers, glycemicLoad, carbohydrateExchangers | all double? via SafeDoubleConverter (camelCase keys) |
| `PhotoAiEstimationPredictFrameInfo (sent as bbox JSON string)` | x, y, width, height | doubles; jsonEncode'd into multipart field 'bbox' |
| `PhotoAiEstimationPredictRequest / ...WithUserFeedbackRequest (internal, not sent as JSON)` | file, fileName, locale, isFromGallery, frameInfo, previousPrediction, userFeedback | Internal app model; source impl explodes it into multipart parts. Not the wire format. |
| `PhotoAiEstimationPredictedDTO` | prediction_id, feedback_id, food_name, reasoning, ingredients_search, image_url, image_id, recipe_id, model_release_version | prediction_id String (required); feedback_id/food_name/reasoning/image_url String?; ingredients_search List<PhotoAiEstimationPredictedIngredientDTO>; image_id & recipe_id via SafeIntConverter (required, int); model_release_version via SafeDoubleConverter (required). features/ai_estimation/data/dto/photo_ai_estimation_predicted_dto.dart |
| `PhotoAiEstimationPredictedIngredientDTO` | foodId, name, weight, type, is_alternative, nutrients | foodId int (SafeInt), name String, weight double (SafeDouble), type FoodType enum (PRODUCT\|RECIPE\|RECIPE_AI\|CUSTOM_ITEM\|PRODUCT_GROUP\|UNKNOWN), is_alternative bool, nutrients PhotoAiEstimationIngredientNutrientsDTO |
| `PhotoAiEstimationPreviousPrediction (sent as previous_prediction JSON string)` | prediction_id, food_name, reasoning, image_url, image_id, recipe_id, ingredients | ingredients: [{name, weight, is_alternative, nutrients:{energy, protein, fat, carbohydrate}}]; jsonEncode'd into multipart field |
| `PhotoDTO` | alt, path, pathThumbnail, pathBigThumbnail, pathLargeThumbnail, ai | core/data/model/photo_dto.dart |
| `Pigeon BillingClientProductQueryResult / BillingClientPurchase (platform channel)` | responseCode, debugMessage, purchases[], products, originalJson, signature | Returned by BillingClientHostApi.getActiveSubscriptions (queryPurchasesAsync with productType 'subs'). mc/i.java, mc/j.java. The Dart side probably forwards originalJson and signature (or the purchaseToken inside originalJson) to the Fitatu backend for subscription validation. That is inferred; confirm in Dart. BillingClientInAppMessageResult has inAppMessageResponseCode, purchaseToken (mc/h.java). BillingClientBillingResult has responseCode, debugMessage (mc/a.java). |
| `PlanRecipeTagDTO` | name, category, translation | features/settings/data/dto/plan_recipe_tag_dto.dart; translation String? |
| `PlannerBasicWeekKcalGoalsDto (local DataStore JSON, NOT HTTP)` | basicWeekKcalGoals | basicWeekKcalGoals is a Map (rc/c.java field type java.util.Map; key type not resolved; inferred day-of-week -> kcal). Stored under collection 'widget', key 'planner_basic_week_kcal_goals' (uc/d.java line 59). Serializer rc/a.java. |
| `PlannerChangesRequest (query for POST days)` | synchronous | bool?, written only if non-null. Two const instances exist: @19ab151 (null, so nothing is sent) and @19ab161 (true). Evidence: planner_changes_request.dart _$PlannerChangesRequestToJson @0xc63084, objs.txt. |
| `PlannerDBO (Realm, local only, NOT sent)` | rid, planDayRevisions, activityPlan, dietPlan, toilet, note, water, waterConsumption, date, lastSynchronizationInMilliseconds, lastLocalUpdateInMilliseconds, synchronizationStatus | core/dbo/planner_dbo.dart. synchronizationStatus enum: unsynchronized / synchronizationSend / synchronized. A Hive shadow copy (fitatu_db_box.planner_box) stores _$PlannerToJson: id, synchronizationStatus, planDayRevisions, activityPlan, dietPlan, toilet, note, water, date, lastSynchronization, lastLocalUpdate. Child DBOs: diet_plan_dbo (meal keys breakfast/secondBreakfast/...; item also stores liquid, recipeAI{id, modelVersion}, mainPhoto), activity_plan_dbo, toilet_dbo, water_dbo, note_dbo(note, tagIds), food_proposal_plan_dbo. |
| `PlannerDTO (GET ~/diet-and-activity-plan/{userId}/day/{date} response)` | planDayRevisions, activityPlan, dietPlan, toilet, water, note, tagsIds | planDayRevisions: List<String>?. activityPlan: List<ActivityPlanDTO>?. dietPlan: Map? (DietPlanDTO). toilet: List<ToiletDTO>?. water: Map (required) {waterConsumption}. note: top-level String?. tagsIds: top-level List (NoteTag ids). The client folds note and tagsIds into a nested note object before _$PlannerDTOFromJson @0xc63b58. |
| `PlannerInfoDto (local DataStore JSON, NOT HTTP)` | caloriesPercentage, caloriesLeft, proteinsPercentage, fatsPercentage, carbohydratesPercentage | All floats. Class name string com.fitatu.tracker.fitatuWidget.data.dto.PlannerInfoDto. Serializer jadx_out/sources/rc/d.java; data class rc/f.java. Dart writes it via PreferenceStorageHostApi.setKeyData(collection='widget', key='planner_<yyyy>_<M>_<d>' with month/day NOT zero-padded, value=JSON string), see uc/d.java line ~116. |
| `PlannerRequest (query for GET day)` | withRating | Always withRating=true (const-folded, _$PlannerRequestToJson @0xc69664). |
| `PlannerSettingsDTO / PlannerAppearanceDTO` | appearance, nutritions, switchers, type, enabled | appearance = {nutritions: [{type: String, enabled: bool}], switchers: [{type, enabled}]} |
| `PlannerSynchronizationDTO (POST days response item)` | date, revision, errorMessage | date: String yyyy-MM-dd. revision: String? (missing revision is logged as 'Missing revision on plannerDay'). errorMessage: String? (logged as 'Revision error ...'). On success the client prepends revision to planDayRevisions. |
| `PlannerUpdateActivityPlanMeasureRequest` | measureId, value | value is a double. |
| `PlannerUpdateApiActivityRequest (planDayActivityType=1, catalog activity)` | uuid, planDayActivityType, activityId, energy, simpleMeasure, advancedMeasures, deletedAt | _$PlannerUpdateApiActivityRequestToJson @0xc71a08. planDayActivityType ints from const map pp+0x1c300: api=1, custom=2, import=3. activityId: int. energy: double. simpleMeasure: {measureId, value} or null (key always present). advancedMeasures: [{measureId, value}]. deletedAt: 'yyyy-MM-dd HH:mm:ss' or null. |
| `PlannerUpdateCustomImportActivityRequest (planDayActivityType=2 custom / 3 import, e.g. Health Connect/Garmin)` | uuid, name, planDayActivityType, originId, energy, distance, activitySource, activityStartedAt, activityStoppedAt, daily, steps, energyTotal, deletedAt | _$PlannerUpdateCustomImportActivityRequestToJson @0xc71588. name falls back to '' in fromEntity. energy and energyTotal: double. activityStartedAt/activityStoppedAt: 'yyyy-MM-dd HH:mm:ss' strings via DateTimeConverter. On the response these are objects {date, timezone}. daily: bool. steps: int?. originId: String? (external source id). activitySource: String?. |
| `PlannerUpdateDietPlanRequest` | breakfast, second_breakfast, lunch, dinner, snack, supper | Each meal key is written only when the meal is non-null (null checks before _set in _$PlannerUpdateDietPlanRequestToJson @0xc70240). Meal value = PlannerUpdateMealRequest {items:[...]}. The local Realm DBO uses 'secondBreakfast', but the wire format uses 'second_breakfast'. |
| `PlannerUpdateMealItemRequest (diet item; product/recipe/custom/AI/quick-add all use this one shape)` | planDayDietItemId, name, foodType, measureId, measureName, measureQuantity, energy, protein, fat, carbohydrate, weight, ingredientsServing, preparationTime, cookingTime, fiber, cholesterol, saturatedFat, sugars, salt, sodium, iron, productId, brand, recipeId, photo, description, rating, deletedAt, source, foodProposalPlan | _$PlannerUpdateMealItemRequestToJson @0xc70638. All keys are always emitted, with null when unset. planDayDietItemId: client-generated String UUID (UUIDUtil.v1 on copy; inferred also on add). Recorded: a new row whose id is not a UUID v1 makes the server reject the whole day ('Invalid pddiId UUID version, only version 1 is acceptable'). foodType: 'PRODUCT' \| 'RECIPE' \| 'RECIPE_AI' (Dart photoAiEstimation) \| 'CUSTOM_ITEM' \| 'PRODUCT_GROUP' \| 'UNKNOWN' (const map pp+0x1c648). measureId: int. measureQuantity: double. productId/recipeId: ids (null when not applicable). photo: String = MainPhoto.bigThumbnail (field_13) of the item's mainPhoto (inferred from field offsets in fromEntity @0xc72190 + main_photo toJson). rating: {rate, votes} or null. deletedAt: 'yyyy-MM-dd HH:mm:ss' via DateTimeConverter (AppDateTimeFormatters.yyyyMMddHHiiss), or null. source: 'API' \| 'GENERATOR' \| 'FOOD_PROPOSAL' (MealSourceSerializer.toJson; fromJson also accepts ints 1/2/3). foodProposalPlan: {planId, planName, planLocale, planRecipeTags:[{name, category, translation}]} or null. The request does NOT send 'liquid', 'recipeAI' or 'mainPhoto' (response-only). |
| `PlannerUpdateMealRequest` | items | items: List<PlannerUpdateMealItemRequest>. In the app it holds ALL items of the meal, including soft-deleted ones (deletedAt set). Recorded: the server keeps rows that are not sent, so this is not required. |
| `PlannerUpdateRequest (value per date in POST ~/diet-plan/{userId}/days body)` | planDayRevisions, activities, dietPlan, toilet, water, note, tagsIds | From PlannerUpdateRequest::toJson @0xc6fe9c. All keys are always present. planDayRevisions: List<String> (the local list of known revisions, newest first). activities: the api-type activities (PlannerUpdateApiActivityRequest) followed by the custom/import ones (PlannerUpdateCustomImportActivityRequest), concatenated with List '+'. Split in fromEntity @0xc6f9f4 by PlanDayActivityType == api. dietPlan: PlannerUpdateDietPlanRequest. toilet: List<PlannerUpdateToiletRequest>. water: {waterConsumption}. note: String? (null after deleteNote). tagsIds: List<int> (NoteTag ids, [] after deleteNote). Example body: {"2026-10-13": {"planDayRevisions":[...], "activities":[...], "dietPlan":{"breakfast":{"items":[...]}}, "toilet":[], "water":{"waterConsumption":1500}, "note":null, "tagsIds":[]}} |
| `PlannerUpdateToiletRequest` | id, form, color, size, smell, mood, time, duration, score, deletedAt | id: String (uuid). Other fields are ints (SafeIntConverter on the DTO side). deletedAt: 'yyyy-MM-dd HH:mm:ss' or null. All keys are always present. |
| `PolarFlowAuthorizationRequest` | code, redirectUrl | redirectUrl constant fitatu://app/fitness-app-auth/polar |
| `PostProductProposalReviewRequest` | accepted | int: 1 = accept, 0 = reject (const instances @19aa211/@19aa201) |
| `PredictProductCategoryRequest` | text, top_k | top_k int, app sends 2 |
| `PredictedIngredientDTO` | foodId, name, weight, liquid, own, generic, favorite, source, foodType, brand, manufacturer, selectedMeasure, nutrients | foodId int(SafeInt), weight double, liquid/own/favorite bool, generic bool?, source enum LAST_USED\|SEARCH, foodType FoodType enum string, brand/manufacturer String?, selectedMeasure PredictedMeasureDTO, nutrients PredictedIngredientNutrientsDTO. Same keys for toJson (used in previous_ingredients). |
| `PredictedIngredientNutrientsDTO` | energy, protein, fat, saturatedFat, carbohydrate, fiber, salt, sugars | double? via SafeDoubleConverter |
| `PredictedMeasureDTO` | measureId, measureWeight, measureQuantity, measureName | measureId int, measureWeight/measureQuantity double, measureName String? |
| `PremiumSettingsRequest` | matchingProcess | Serialized as literal false in this build. |
| `ProductCategoryPredictDTO` | enabled, results, results[].text, results[].predictions, predictions[].category, predictions[].parent_category, predictions[].confidence, predictions[].category_id, predictions[].parent_id | snake_case keys; confidence double, ids SafeInt |
| `ProductDTO` | id, foodType, name, private, deleted, containNutritions, genericProduct, duplicate, hasBarcodes, editable, favorite, liquid, energyCalories, energy, currentUserPrivateBarcode, protein, vegetableProtein, animalProtein, fat, saturatedFat, polyunsaturatedFat, monounsaturatedFat, carbohydrate, cholesterol, fiber, sodium, salt, calcium, potassium, iron, magnesium, zinc, phosphorus, iodine, copper, selenium, vitaminA, vitaminB1, vitaminB2, vitaminB5, vitaminB6, vitaminB7, vitaminB12, vitaminC, vitaminD, vitaminE, vitaminK, vitaminPP, folicAcid, sugars, omega3, omega6, caffeine, carbohydrateExchangers, proteinFatExchangers, biotin, brand, measures, rawIngredients, initialMeasure, simpleMeasures, category, barcode, proposals, ingredients, productScore, userId, visible, manufacturer, accessType | product_dto.dart _$ProductDTOFromJson. id SafeInt; foodType FoodType string enum; all nutrient fields nullable double via SafeDoubleConverter; category,userId SafeInt?; measures=List<MeasureDTO{id,name,energyPerUnit,weightPerUnit}>; initialMeasure=InitialMeasureDTO{key,weight,unitKey}; simpleMeasures=List<SimpleMeasureDTO{id,name,portion,energy,isLiquid,userMeasure,weight}>; proposals=List<ProposalDTO{id:String,propertyName,propertyValue,userId,createdAt}>?; ingredients=List<IngredientDTO{id:String,name,hasDetails,affectsScore,eNumber,healthRating:int?,position:int?,categories:[CategoryDTO{id,name}]}>?; productScore=ProductScoreDTO{value:double?} (this is the Fitatu score); accessType int enum. |
| `ProductFormRequest` | name, brand, energy, fat, carbohydrate, protein, measures, labels, saturatedFat, sugars, fiber, salt, barcode, monounsaturatedFat, polyunsaturatedFat, omega3, omega6, cholesterol, caffeine, vitaminA, vitaminB1, vitaminB2, vitaminB5, vitaminB6, vitaminB7, folicAcid, vitaminB12, vitaminC, vitaminD, vitaminE, vitaminPP, vitaminK, zinc, phosphorus, iodine, magnesium, copper, potassium, selenium, sodium, calcium, iron, vegetableProtein, animalProtein, category, rawIngredients, originProductId | product_form_request.dart @0xdd1814: Map literal of 47 pairs, nulls INCLUDED. energy/fat/carbohydrate/protein non-null double; category non-null int (Smi); measures=List<ProductMeasureRequest{measureKey,measureUnit,weight:double}>; labels=List<String>? from Map<ProductFormLabel,String> {PACKAGE_LABEL_PHOTO}; originProductId nullable. |
| `ProductIngredientDTO` | id, name, origin, additionalData, originDescription, usageFood, usageOther, acceptableDosage, metabolism, effects, sources, eNumber, healthRating, categories | id String; healthRating int?; categories=List<ProductIngredientCategoryDTO{id:int,symbol:String}>? |
| `ProfileSetupRequest` | userSettings, userDietSettings, weekMealSchema | Nested as 'settings' in the sign-in and sign-up bodies (UserSettingsDTO, UserDietSettingsDTO, WeekMealSchemaDTO). |
| `PromotionDTO` | id, startAt, endAt, plans, type, config, accessGroup | type is PromotionType: basic, after-registration, targeted-banner, target-group, web-banner, new-year-resolution, timed, other. config = {banner, popup, duration, startAfter, endAfter, oldPrice, analyticsPrefix?}. |
| `PurchaseOwnerInfoDTO` | owned, demo, subscription | subscription -> PurchasePlanDTO. |
| `PurchasePlanDTO` | id, startDate, endDate, planName | id SafeInt; dates String. |
| `PurchaseRequest` | id, title, transaction, additionalData | id = store productID; title always ''; additionalData = {developerPayload: userId}. Built by PurchaseRequest.fromProductTransaction(purchase, userId). |
| `RecipeCatalogDTO / RecipeCategoryDTO / RecipeCategoryItemDTO` | categories, id, name, translatedName, recipes, recipes[].id, recipes[].name, recipes[].accessType, recipes[].reviewsCount, recipes[].ratingCount, recipes[].preparationTime, recipes[].cookingTime, recipes[].energy, recipes[].energyCalories, recipes[].premiumInRecipesCatalog, recipes[].mainPhoto, recipes[].ingredientsNames, recipes[].rating, recipes[].favorite, recipes[].source | recipe_catalog_dto.dart; RecipeCategoryDTO.id is String; item.mainPhoto=PhotoDTO? |
| `RecipeCatalogRequest` | foodProposalPlanId | query only; omitted when null |
| `RecipeDetailsDTO` | id, name, recipeDescription, weight, energy, energyCalories, protein, vegetableProtein, animalProtein, fat, saturatedFat, polyunsaturatedFat, monounsaturatedFat, carbohydrate, cholesterol, fiber, sodium, calcium, salt, potassium, iron, magnesium, zinc, phosphorus, iodine, copper, selenium, vitaminA, vitaminB1, vitaminB2, vitaminB5, vitaminB6, vitaminB7, vitaminB12, vitaminC, vitaminD, vitaminE, vitaminK, vitaminPP, folicAcid, sugars, omega3, omega6, caffeine, deleted, source, mealSchema, serving, userId, carbohydrateExchangers, proteinFatExchangers, preparationTime, cookingTime, categories, items, premiumInRecipesCatalog, availableInRecipesCatalog, shared, simpleMeasures, measures, favorite, tags, username, editable, photos, rating, accessType | recipe_details_dto.dart. mealSchema=List<MealType string>; categories=List<CategoryType> decoded from ints 1..103; serving SafeDouble; source/userId SafeInt; items=List<RecipeItemDTO>; simpleMeasures=List<RecipeUserMeasureDTO{id,portion,energy,weight}>; measures=List<MeasureDTO>; tags=List<PlanRecipeTagDTO>?; photos=List<RecipePhotoDTO>; rating=RatesDTO{rating:{rate:double,votes:int}, userVote:int?} |
| `RecipeItemDTO` | itemId, name, measureId, measureName, measureQuantity, liquid, measureWeight, energy, hint, productId, recipeId | recipe_item_dto.dart (FromJson order) |
| `RecipePhotoDTO` | id, path, alt, authorId, isMain, step, isIngredientsPhoto, sizes, ai, sizes.small, sizes.medium, sizes.large, sizes.gallery | recipe_photo_dto.dart |
| `RefreshUserTokenRequest` | refresh_token | Body of POST ~/token/refresh. Snake_case only; the client does not send the other variants the current Python client tries. |
| `RegisteredUserInfoDTO` | id, email | Response of POST ~/users. |
| `ReportProductRequest / ReportRecipeRequest` | message | ReportRecipeRequest.toJson calls _$ReportProductRequestToJson |
| `ResolutionDTO` | id, name, translationTag, createdAt, userValue | id: int. userValue: String? (the user's custom value). |
| `ResolutionsSimpleStatsDTO` | countAllUsersUsedResolutions |  |
| `ReviewDTO` | id, itemId, content, locale, typeId, visible, authorGender, nickname, userId, createdAt, updatedAt, rating | id String; typeId int: 1 product, 2 recipe, 3 activity, 10 diet, 11 foodProposalPlan, 12 intermittentFasting, 13 resolution. |
| `ReviewsDTO` | count, lastUserReviewId, locales, reviews | locales Map<String,int>. |
| `ReviewsRequest` | itemId, page, limit, locale | QUERY params; limit hard-coded 20. |
| `SearchActivityItemDTO` | activityId, name, type, intensity, energy, measuresDescription, measures, uuid | type ActivityType int: 0 import,1 strength,2 cardio,3 stretching; intensity int: 1 low,2 mid,3 high,4 higher; measures[ActivityMeasureDTO {measureId,name,short,type,scale,selected,value,defaultValue}]. |
| `SearchActivityRequest` | page, limit, locale, bmr, sex | Sent as QUERY params on GET; each omitted when null. sex mapped to int 0/1/2/9. |
| `SearchActivityRequest (query)` | page, limit, locale, bmr, sex | All fields optional. sex is a Sex int. |
| `SearchFoodCatalogItemDTO` | foodId, name, containNutritions, type, brand, energy, measure, accessType, source, photo, cookingTime, preparationTime, rating, reviewsCount, ratingCount, liquid, manufacturer, premiumInRecipesCatalog, generic | search_food_catalog_item_dto.dart; photo=PhotoDTO (nullable), measure=FoodMeasureDTO |
| `SearchFoodItemDTO` | isInitialFood, availableInRecipesCatalog, premiumInRecipesCatalog, favorite, generic, containNutritions, accessType, numberOfUse, userNumberOfUse, lang, type, foodId, name, brand, measure, rating, reviewsCount, ratingCount, score, extraScore, mergedScore, lastUsed, index, mainPhoto, items, cookingTime, preparationTime, energy, tags, meals, source, liquid, manufacturer | search_food_item_dto.dart. ints via SafeIntConverter, doubles via SafeDoubleConverter; type=FoodType string; accessType int map; index=SearchIndexType (SEARCH\|LAST_USED\|other); measure=FoodMeasureDTO; mainPhoto=PhotoDTO.fromJsonSafe; items=List<SearchRecipeItemDTO{id,name}>?; tags,meals=List<String>; lastUsed String? |
| `SearchFoodRequest (query map)` | page, limit, locale, hasFilters, accessType[], phrase, targetEnergy, energy, targetProtein, targetFat, targetCarbohydrate, containNutritions, independentDish, generic, searchOnlyInOneLocale, excludedProductCategories[], type, minEnergy, maxEnergy, minProtein, maxProtein, minFat, maxFat, minCarbohydrate, maxCarbohydrate, minCookingTime, maxCookingTime, tags[], meal, meals[], ingredients[], shuffleWithSeed, source[], foodProposalPlan[] | features/search_catalog/data/request/search_food_request.dart _$SearchFoodRequestToJson @0xc8c6a4: every key written only if non-null. type=FoodType string (PRODUCT\|RECIPE\|RECIPE_AI\|CUSTOM_ITEM\|PRODUCT_GROUP\|UNKNOWN); meal and meals[] = MealType strings (breakfast\|second_breakfast\|lunch\|dinner\|snack\|supper); source[] = FoodSourceType int ids via FoodSourceTypeConverter; accessType[] list of strings (bloc uses FREE, PREMIUM). min/max* come from range filters (energy/protein/fat/carbohydrate/cookingTime). |
| `SearchRecipeCatalogItemDTO` | foodId, type, name, brand, source, accessType, energy, favorite, premiumInRecipesCatalog, containNutritions, preparationTime, cookingTime, rating, reviewsCount, ratingCount, measure, photo, saturatedFat, sugars, salt, sodium, iron, fiber, liquid | search_recipe_catalog_item_dto.dart; used for catalog recipes and food-proposal results |
| `SettingsDTO` | userId, date, userSettings, calculatedValues, weekMealSchema, foodProposalSettings, plannerSettings, userDietSettings, waterSettings, weekMealRecommendedEnergy, updatedAt | features/settings/data/dto/settings_dto.dart _$SettingsDTOFromJson @~0xd32ee8. userId uses the SafeInt converter. date is parsed with DateTime.parse. foodProposalSettings and updatedAt are nullable. |
| `SettingsPartialUpdateRequest` | userId, updatedAt, userDietSettings, userSettings, waterSettings, plannerSettings, weekMealSchema, foodProposalSettings | Request body for PATCH settings-new. userId (int) and updatedAt (DateTime.toIso8601String() or null) are always sent. Every other section is omitted when null. |
| `SharedFoodDTO` | mealsDate, items | items[{name, measureId, measureQuantity, meal, type, measureName?, itemId?, measureWeight?, ingredientsServing?, customItem?}]. |
| `SignInByEmailRequest` | _username, _password, settings | 'settings' (ProfileSetupRequest) is only included when non-null. |
| `SignInByFacebookRequest` | email, facebookUserAccessToken, facebookUserId, locale, searchLocale, partnerId, tokenType, settings, acceptTerms, sensitiveAccepted, marketingAccepted, acceptAgeRequirements | partnerId is the constant 07b92206-189c-11e8-81d0-0242ac120007. tokenType is LIMITED or FULL. email, searchLocale, tokenType and settings are omitted when null. |
| `SignInGuestRequest` | locale, searchLocale, settings, acceptTerms, sensitiveAccepted, marketingAccepted, acceptAgeRequirements | Body of POST ~/login-demo for guest sign-up. |
| `SignUpByEmailRequest` | email, password, locale, searchLocale, settings, acceptTerms, sensitiveAccepted, marketingAccepted, acceptAgeRequirements | Body of POST ~/users. |
| `SummaryActivitiesDTO` | energy, activities, summaryMeasures, count, measures, measureId, value | activities: [{count, measures:[{measureId, value}]?}]. summaryMeasures: [{measureId, value}]. |
| `SummaryNutritionDetailsDTO` | measures, targets, targetRanges, min, max | All three are maps keyed by date string. measures→double?, targets→double (whole map nullable), targetRanges→{min,max}. |
| `SummaryNutritionProductsDTO` | list, count, name, amount, current, per100 | {list:[{name:String, amount:{current:num, per100:num?}}], count:int} |
| `SummaryNutritionProductsRequest (query)` | fromDate, toDate, page, limit | All four keys are always in the literal map (no null filtering). |
| `SummaryNutritionsDTO` | energy, dietEnergy, activityEnergy, addedActivityEnergy, protein, vegetableProtein, animalProtein, fat, saturatedFat, polyunsaturatedFat, monounsaturatedFat, carbohydrate, cholesterol, fiber, sodium, calcium, potassium, iron, magnesium, zinc, phosphorus, iodine, copper, selenium, vitaminA, vitaminB1, vitaminB12, vitaminB2, vitaminB5, vitaminB6, vitaminB7, vitaminC, vitaminD, vitaminE, vitaminK, vitaminPP, folicAcid, sugars, omega3, omega6, caffeine, salt | Item shape {min, max, current}. dietEnergy/activityEnergy/addedActivityEnergy are {max} only (SummaryNutritionPartItemDTO). |
| `SummaryRequest (query)` | fromDate, toDate | Both yyyy-MM-dd via DateConverter (AppDateTimeFormatters.yyyyMMdd). |
| `TechnicalBreakDTO (response header 'technical-break', base64 JSON, quotes stripped)` | displayFromDate, displayToDate | If active, the response is rejected and the app enters AppStateTechnicalBreak. |
| `TechnicalBreakNotificationDto (response header 'technical-break-notification', base64 JSON)` | displayFromDate, displayToDate, message | message is {pl_PL, de_DE, en_GB, es_ES, fr_FR, it_IT, nl_NL}. |
| `TextAiPredictRequest` | text, locale | JSON body of POST ~/text-ai/predict |
| `TextAiPredictWithUserFeedbackRequest` | prediction_id, locale, previous_ingredients, user_feedback, previous_input_text | previous_ingredients is a JSON array of PredictedIngredientDTO objects |
| `TextAiPredictedDTO` | prediction_id, ingredients | prediction_id String? default ""; ingredients List<PredictedIngredientDTO> |
| `ToiletDTO (response)` | id, form, color, size, smell, mood, time, duration, score, deletedAt | id: String. Others use SafeInt. deletedAt: String? parsed as UTC. |
| `TransactionRequest` | type, id, signature, receipt, appStoreTransaction, purchaseToken | type always present: 'android-playstore' or 'ios-appstore'; others omitted when null. Android: id=orderId, signature=signature, receipt=originalJson, purchaseToken=purchaseToken (PurchaseWrapper field mapping inferred from field offsets). iOS: id=purchaseID, appStoreTransaction=verificationData.serverVerificationData. |
| `UpdateHabitRequest (PUT body)` | habitItems, completed, enrolledDate | habitItems: List<HabitCompletedDayDTO>.toJson. completed: bool. enrolledDate: DateConverter, yyyy-MM-dd. App usage from habits_in_memory_repository_impl.dart: add and restart send habitItems=[], completed=false and enrolledDate=today; finish sends completed=true; toggle sends the full updated list of checked days with completed=false |
| `UpdatePhotoAiEstimationRequest` | name, items | items[{itemId:int, measureId:int, measureQuantity:double, type:FoodType string}]. |
| `UpdateReviewContentRequest` | content |  |
| `UpdateUserRequest` | nickname, locale, searchLocale, marketingAccepted, appVersion, systemInfo, systemVersion | All keys omitted when null (includeIfNull:false). |
| `UpdatedRecipeDTO` | id, name, energy, fat, protein, carbohydrate | response of POST/PUT recipes |
| `UpdatedRecipeRequest (add & update recipe body)` | name, items, tags, serving, shared, recipeDescription, cookingTime, preparationTime, mealSchema, categories | add_recipe_request.dart @0xce2bf8: map literal, nulls INCLUDED. items=List<AddRecipeItemRequest{itemId:int, measureId:int, measureQuantity:double, type:"PRODUCT"}> (type hardcoded "PRODUCT"); tags=List<{name,category,translation}>? (_$PlanRecipeTagToJson); mealSchema list (List<String> inferred from AddRecipeParams ==); preparationTime and categories are never set by Add/UpdateRecipeUseCase (sent as null). |
| `UserDietSettingsDTO` | activityEnergyInclusionMode, manualEnergyTarget, intermittentFastingConfig, activityBase, activityTraining, weightChangeSpeed, weightChangeSpeedKg, weightChangeSpeedUnit, weightChangeDirection, nutritionSchema, energy, proteinPercentage, proteinWeight, fatPercentage, fatWeight, carbohydratePercentage, carbohydrateWeight | These are the goals and macro targets. activityEnergyInclusionMode is an int (SafeInt). manualEnergyTarget is bool. activityBase and activityTraining are double (PAL components, SafeDouble). weightChangeSpeedUnit is a BodyMeasureUnit int. weightChangeDirection is a UserWeightGoalType int: reduction=-1, maintenance=0, gain=1. energy is an int kcal target. Macros are given as both percentage and grams. nutritionSchema is a per-weekday override map with keys monday..sunday, each value a NutritionSchemaItemDTO or null. The fromJson tolerates a List (an empty [] from PHP). |
| `UserGameProfileDTO` | currencies, resources, streak | currencies: Map<String, GameCurrencyDTO{type: GameCurrencyType (JEWELS, UNKNOWN), amount}>. resources: Map<String, UserGameResourceDTO{type: UserGameResource (SHIELD, UNKNOWN), amount, currentLimit, maxLimit}>. streak: UserGameStreakDTO{current, longest, date: String, startDate}. |
| `UserHabitDTO` | completed, enrolledDate, habit, habitItems | completed: bool. enrolledDate: String (Date.create). habit: HabitDTO object. habitItems: List<HabitCompletedDayDTO> |
| `UserInfoDTO` | id, username, nickname, email, sex, locale, searchLocale, storageLocale, createdAt, roles, searchUrls, demo, marketingAccepted, enabled, matchingProcess, systemInfo, systemVersion, appVersion, accessControl | features/user/data/dto/user_info_dto.dart. id/username/email/locale/searchLocale/storageLocale/createdAt: String (required casts); nickname String?; sex via SafeIntConverter (int); roles/searchUrls/accessControl List<String>?; demo/marketingAccepted/enabled bool; matchingProcess enum 'DIET'\|'PREMIUM'\|'premiumAi' nullable; systemInfo/systemVersion/appVersion String? (default ''). |
| `UserInfoDTO (only the fields that feed headers)` | id, username, nickname, email, sex, locale, searchLocale, storageLocale, createdAt, roles, searchUrls, demo, marketingAccepted, enabled, matchingProcess, systemInfo, systemVersion, appVersion, accessControl | searchLocale -> APP-SearchLocale, storageLocale -> APP-StorageLocale and API-Cluster. storageLocale is also persisted as the '~' host locale. searchUrls is only read by advertisement_bloc and is not used for routing. |
| `UserInfoDto (local DataStore JSON, NOT HTTP)` | isLogged, isPremium, isPremiumAi | Stored under collection 'user', key 'info' (uc/d.java line 171; collection literal is SentryBaseEvent.JsonKeys.USER = "user"). Serializer rc/g.java, data class rc/i.java. |
| `UserMetaDTO / UserResolutionsDTO` | newYearResolutions2026, fb, ig, tt, hb, sh, kn, yt | newYearResolutions2026 is a JSON string holding {fb, ig, tt, hb, sh, kn, yt}. fb, ig, hb, sh and kn are bool; tt and yt are bool?. The meaning of the abbreviations is not visible in code (possibly channel or campaign flags; unverified). |
| `UserNotificationTokenRequest` | notificationToken | FCM token. |
| `UserResolutionsRequest / UserResolutionRequest` | resolutions, newResolutions, id, value | resolutions = [{id, value}]. newResolutions element type not confirmed (no nested toJson; likely List<String>). |
| `UserSearchFeedbackRequest` | locale, rating, searchQuery, module, version, searchResults, searchFilters, topic, explanation | searchResults[{foodId, foodType, foodName}]; searchFilters {minEnergy,maxEnergy,minProtein,maxProtein,minFat,maxFat,minCarbohydrate,maxCarbohydrate}. |
| `UserSettingsDTO` | birthDate, height, heightCm, weightStart, weightStartKg, weightCurrent, weightCurrentKg, weightTarget, weightTargetKg, age, heightUnit, weightCurrentUnit, weightStartUnit, weightTargetUnit, sex | Unit fields are BodyMeasureUnit ints: cm=1, ftInches=2, inches=3, kg=4, stLb=5, lb=6. sex is a Sex int: notKnown=0, male=1, female=2, notApplicable=9. height and weight values are in the user's unit, with *Cm/*Kg as metric mirrors. |
| `UserSubscriptionHistoryDTO / UserSubscriptionDTO` | subscriptions, id, startDate, endDate, planName, planGroupType, provider, providerId, duration, description | id is numeric. The rest are strings (description nullable). Types other than id and description are inferred. |
| `VoiceAiPredictRequest (internal)` | audio, audioName, locale | audio bytes -> multipart 'audio' part, audio/m4a, filename=audioName |
| `VoiceAiPredictedDTO` | prediction_id, transcription, ingredients | prediction_id String? defaulted to ""; transcription String; ingredients List<PredictedIngredientDTO> |
| `VoiceAiPredictedWithUserFeedbackDTO` | prediction_id, feedback_id, ingredients, valid_input, transcription | prediction_id default ""; feedback_id String?; valid_input bool?; transcription String? |
| `Water (planner water, request and response)` | waterConsumption | Request: _$WaterToJson {waterConsumption: num(int, ml)}. Response: WaterDTO waterConsumption via SafeIntConverter. The app has no separate water call; water travels inside the day snapshot. The web route ~/water/{userId}/{date} (GET/PUT, used by this library's client.water) is not called by the app. |
| `WaterSettingsDTO` | unitCapacity | unitCapacity is a number (ml per glass or unit). No explicit water goal key exists in this DTO. |
| `WebToken (JWT payload, decoded client-side with jwt_decoder)` | id, appId, email, username, banned, createdAt, enabled, ip, roles, exp | The user id used in ~/users/{userId}/... paths is the JWT 'id' claim. The client never verifies the signature. |
| `WeekMealSchemaDTO / DayMealSchemasDTO / DayMealSchemaDTO` | default, dayMealSchemaCustomList, meals, active, label, meal, mealName, mealTime, selected | weekMealSchema = {default: {meals: Map<String, DayMealSchemaDTO>}, dayMealSchemaCustomList: Map<String, {meals: ...}>}. The custom-list key format is not confirmed (likely weekday or date). DayMealSchemaDTO: active is bool; label is MealType (breakfast, second_breakfast, lunch, dinner, snack, supper); meal is an int id; mealName and mealTime are strings; selected is bool. |
| `WeekMealsRecommendedEnergyDTO` | default, custom, meals, dayOfWeek, mealId, mealLabel, recommendedPercent | Shape: {default: {meals: [{mealId, mealLabel: MealType, recommendedPercent}]}, custom: [{dayOfWeek, meals: [...]}]} |
| `WeightMeasurementDTO (response item)` | date, value, difference, direction | date: String. value: double. difference: nullable double. direction: int enum UserWeightGoalType with 1=gain, -1=reduction, 0=maintenance |
| `WeightMeasurementsChartDTO (response)` | weights, targets, weightUnit | weights is a Map<String,double>; EmptyListToEmptyMapConverter accepts [] as an empty map. targets is a Map<String,double> whose values go through SafeDoubleConverter. weightUnit is a String |
