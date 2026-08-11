# 侧边栏 Tab 排序规则

> **强制规范**：凡是调用 `app.extensionManager.registerSidebarTab` 注册侧边栏 Tab 的插件，**必须**同时完成本文的两个步骤，否则 Tab 在侧边栏中的位置每次刷新都是随机的。

## 问题背景

ComfyUI 前端用 `Promise.allSettled` **并行动态 import** 所有插件的 JS 文件，谁先加载完谁先注册，顺序完全随机。官方 `registerSidebarTab` API 的配置项只有 `id/icon/title/tooltip/type/render`，**没有任何排序参数**。因此需要约定式排序规则：插件自行声明 `order` 数字，由内嵌的排序代码统一重排。

## 规则说明

- 每个插件在 `registerSidebarTab` 配置对象中额外声明 `order: 数字`，**数字小的排前面**；
- 两个插件 `order` 相同时，按 Tab 的 `id` 字母序兜底（避免平局时随机）；
- 未声明 `order` 的 Tab（含官方节点库、模型库等）一律不动；
- 排序代码内嵌在**每个插件自己的入口文件末尾**（不依赖独立文件、不维护中心化列表），用 `window.__comfySidebarTabSorterInstalled` 全局标记防重入——多个插件共存时只有第一个加载的执行排序，卸载任意插件不影响其余插件。

## 步骤一：注册时声明 order

```js
app.extensionManager.registerSidebarTab({
    id: "my-plugin-sidebar",
    order: 50, // 侧边栏排序规则：数字小的排前面
    icon: "pi pi-star",
    title: "我的插件",
    tooltip: "我的插件",
    type: "custom",
    render: (container) => { /* ... */ },
});
```

### order 取值约定（已占用值登记）

| order | 插件 |
|---|---|
| 10 | NodeCraft AI |
| 20 | ComfyUI-Ranking（社区精选） |
| 30 | Flying-Translation2.0（飞行翻译） |
| 40 | ComfyUI-Music-player（飞行音乐） |

新插件从 **50 起**按 10 递增取值；想插在中间就用 15、25 这类中间值，无需改动其他插件。

## 步骤二：入口文件末尾内嵌排序代码

将下面这段代码**原样追加**到插件前端入口文件（即调用 `registerSidebarTab` 的那个文件）末尾。前提：文件顶部已有 `import { app } from "../../scripts/app.js";`（相对路径按插件目录层级调整）。

```js
// ── 侧边栏 Tab 排序规则（各插件入口文件内嵌同一段代码，全局标记保证只执行一次） ──
// 规则：registerSidebarTab 声明 order 数字，小的排前面；未声明的（含官方 Tab）一律不动
// 实现：每秒轮询一次（最长 30 秒），等声明 order 的 Tab 集合连续两轮稳定后重排，兼容插件多时的慢加载
if (!window.__comfySidebarTabSorterInstalled) {
    window.__comfySidebarTabSorterInstalled = true;
    console.debug("[侧边栏排序] 规则已安装，开始轮询等待各插件 Tab 注册…");
    let 上次指纹 = null;
    let 已耗时 = 0;
    const 轮询间隔 = 1000;
    const 定时器 = setInterval(() => {
        已耗时 += 轮询间隔;
        if (已耗时 > 30000) {
            clearInterval(定时器);
            console.debug("[侧边栏排序] 轮询结束（30秒）");
            return;
        }
        const 管理器 = app.extensionManager;
        if (!管理器?.getSidebarTabs || !管理器.unregisterSidebarTab || !管理器.registerSidebarTab) return;
        const 全部 = 管理器.getSidebarTabs();
        if (!Array.isArray(全部)) return;
        const 参与者 = 全部.filter((t) => typeof t?.order === "number");
        // 集合有变化说明还有插件在陆续注册，等下一轮稳定后再排
        const 指纹 = 参与者.map((t) => t.id).sort().join();
        if (指纹 !== 上次指纹) {
            上次指纹 = 指纹;
            return;
        }
        if (参与者.length < 2) return;
        // 若用户已点开某个参与排序的 Tab，本轮跳过，避免注销激活面板
        const 状态 = 管理器.sidebarTab?.value ?? 管理器.sidebarTab;
        if (状态?.activeSidebarTabId && 参与者.some((t) => t.id === 状态.activeSidebarTabId)) return;
        const 排序后 = [...参与者].sort((a, b) => a.order - b.order || String(a.id).localeCompare(String(b.id)));
        const 已有序 = 参与者.map((t) => t.id).join() === 排序后.map((t) => t.id).join()
            && 全部.slice(-参与者.length).every((t) => typeof t?.order === "number");
        if (已有序) return;
        for (const t of 排序后) 管理器.unregisterSidebarTab(t.id);
        for (const t of 排序后) 管理器.registerSidebarTab(t);
        console.debug("[侧边栏排序] 已按 order 固定 Tab 顺序:", 排序后.map((t) => `${t.id}(${t.order})`));
    }, 轮询间隔);
}
```

## 实现原理

1. `unregisterSidebarTab(id)` 后再 `registerSidebarTab(tab)`，Tab 会追加到内部数组末尾——按目标顺序依次重注册即实现排序；
2. 排序采用**每秒轮询**（最长 30 秒）而非单次固定延迟：插件多的环境加载耗时长，固定延迟（如 2 秒）会在其他插件的 Tab 注册完成之前触发，导致排序失效；
3. 轮询要求声明 `order` 的 Tab 集合**连续两轮稳定**（没有新 Tab 加入）才执行重排，避免排完之后又有插件插队；
4. `render` 是点击 Tab 时才懒执行的，启动阶段重注册没有副作用；若用户已点开某个参与排序的面板，当轮跳过，等面板关闭后的下一轮再排。

## 注意事项

- **不要**创建独立的排序器 JS 文件，代码必须内嵌在各插件自己的入口文件里；
- **不要**在排序代码中硬编码任何 Tab id 列表；
- 排序代码在所有插件中必须保持**完全一致**（含全局标记名 `__comfySidebarTabSorterInstalled`），修改逻辑时需同步更新所有插件；
- 验证方式：控制台切到 Verbose 级别，刷新后应看到 `[侧边栏排序] 已按 order 固定 Tab 顺序: [...]`。
