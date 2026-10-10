const $ = id => document.getElementById(id);
let port;
$('install').textContent = `python -m video_notes "视频链接"`;
const source = Number(new URL(location.href).searchParams.get('source'));
function videoURL(value) {
  const u = new URL(value);
  if (u.protocol !== 'https:' || u.username || u.password) throw new Error('请在 HTTPS 视频页面使用扩展。');
  const h = u.hostname;
  if ((h === 'bilibili.com' || h.endsWith('.bilibili.com')) && /^\/video\/(BV[A-Za-z0-9]+|av\d+)/.test(u.pathname)) return {url:u.href, domain:'bilibili.com'};
  if ((h === 'youtube.com' || h.endsWith('.youtube.com')) && ((u.pathname === '/watch' && u.searchParams.get('v')) || /^\/(shorts|live)\/[\w-]+/.test(u.pathname))) return {url:u.href,domain:'youtube.com'};
  if (h === 'youtu.be' && /^\/[\w-]+$/.test(u.pathname)) return {url:`https://www.youtube.com/watch?v=${encodeURIComponent(u.pathname.slice(1))}`,domain:'youtube.com'};
  if (h === 'douyin.com' || h === 'www.douyin.com') {
    const match = /^\/video\/(\d+)\/?$/.exec(u.pathname);
    const videoId = match?.[1] || u.searchParams.get('modal_id');
    if (videoId && /^\d+$/.test(videoId)) return {url:`https://www.douyin.com/video/${videoId}`,domain:'douyin.com'};
  }
  throw new Error('当前扩展暂不支持此链接，请使用已适配网站的单个视频详情页。');
}
chrome.tabs.get(source).then(t => { $('video').textContent = t.url; videoURL(t.url); }).catch(() => { $('status').textContent='请从已适配网站的视频详情页点击扩展。'; $('start').disabled=true; });
$('start').addEventListener('click', async () => {
  $('start').disabled=true; $('result').textContent=''; $('files').textContent=''; $('log').textContent='';
  let completed=false;
  try {
    const tab=await chrome.tabs.get(source);
    const target=videoURL(tab.url);
    const stores=await chrome.cookies.getAllCookieStores();
    const store=stores.find(s=>s.tabIds.includes(source));
    if (!store) throw new Error('无法确定视频所在的浏览器 Cookie 配置，请重新打开视频页面。');
    // Only unpartitioned cookies: Netscape/CookieJar cannot preserve partition semantics.
    const cookies=(await chrome.cookies.getAll({domain:target.domain,storeId:store.id})).filter(c=>!c.partitionKey);
    $('status').textContent='正在连接本机 VideoNotes…';
    port=chrome.runtime.connectNative('com.videonotes.host');
    port.onMessage.addListener(m=>{
      if (m.type==='progress') { $('status').textContent=m.message; $('log').textContent=($('log').textContent+'\n'+m.message).slice(-16000); }
      if (m.type==='result' || m.type==='error') {
        completed=true; $('start').disabled=false; $('status').textContent=m.message;
        $('result').textContent=m.preview || '';
        $('files').textContent=(m.files || []).join('\n');
        port.disconnect(); port=null;
      }
    });
    port.onDisconnect.addListener(()=>{
      const error=chrome.runtime.lastError;
      if (!completed) { $('status').textContent='本机连接中断或尚未安装。请展开“首次安装”完成注册；已下载的媒体仍保留。'; $('start').disabled=false; }
      port=null;
    });
    port.postMessage({version:1,url:target.url,cookies,fallback:$('fallback').checked?'transcribe':'stop'});
  } catch(e) { if(port)port.disconnect(); $('status').textContent=e.message; $('start').disabled=false; }
});
