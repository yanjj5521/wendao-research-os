/* 云服务公共配置 —— 由 workbuddy_cloud_service 激活时返回的 publicConfig 生成。
   仅包含可公开的端用户配置（publishableKey 不携带权限，服务端按应用域名校验）。
   若应用域名变化，重新激活后用新的 publicConfig 更新本文件。 */
window.RESEARCH_CLOUD_CONFIG = {
  endpoint: "https://wendao-research.app.workbuddy.host",
  oauthRelayBaseUrl: "https://www.workbuddy.cn/v2/as/genie-baas/oauth",
  publishableKey: "wbpk_exnj5mbA7SbiwzyBfyZWHw_YdKSknmZ5DfiLuTo6D9cWON9RtECYyI5"
};
