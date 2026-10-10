let bridge;
function connectBridge() {
  if (bridge) return;
  const connection = chrome.runtime.connectNative('com.videonotes.bridge');
  bridge = connection;
  connection.onDisconnect.addListener(() => { const ignored=chrome.runtime.lastError; bridge=null; });
  connection.onMessage.addListener(async message => {
    if(message.type !== 'cookies_request') return;
    try {
      const url=new URL(message.url);
      const h=url.hostname;
      const domain=(h==='bilibili.com'||h.endsWith('.bilibili.com'))?'bilibili.com':
        (h==='youtube.com'||h.endsWith('.youtube.com'))?'youtube.com':
        (h==='douyin.com'||h.endsWith('.douyin.com'))?'douyin.com':null;
      if(url.protocol!=='https:' || !domain) throw new Error('Unsupported site');
      // Use this extension profile's regular cookie store, independent of active tab.
      const cookies=(await chrome.cookies.getAll({domain})).filter(c=>!c.partitionKey);
      connection.postMessage({type:'cookies_response',id:message.id,cookies});
    } catch (_) {
      connection.postMessage({type:'cookies_error',id:message.id});
    }
  });
}
chrome.runtime.onInstalled.addListener(connectBridge);
chrome.runtime.onStartup.addListener(connectBridge);
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='bridge-reconnect')connectBridge();});
chrome.alarms.create('bridge-reconnect',{periodInMinutes:0.5});
connectBridge();
// Optional status/legacy panel; CLI runs do not open or require it.
chrome.action.onClicked.addListener(async tab => {
  await chrome.tabs.create({url:chrome.runtime.getURL(`panel.html?source=${tab.id}`)});
});
