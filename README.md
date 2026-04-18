# Limbus Wiki Search Plugin

边狱公司中文维基搜索插件，用于搜索边狱公司中文维基并发送页面截图。

## 功能

- 搜索边狱公司中文维基内容
- 支持返回多个搜索结果供选择
- 自动截图并发送选中的维基页面
- 支持使用curl_cffi或httpx进行网络请求

## 安装

1. 将插件文件夹 `limbus_wiki_search` 复制到 AstrBot 的插件目录
2. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
3. 安装 Playwright 浏览器：
   ```bash
   playwright install
   ```

## 使用

在聊天中输入以下命令：

```
wiki [关键词]
```

例如：
```
wiki 良秀
```

插件会返回相关的搜索结果，然后您可以回复数字选择要查看的页面。

## 依赖

- Python 3.8+
- httpx >= 0.25.0
- playwright
- curl_cffi (可选，用于更好的请求模拟)

## 注意事项

- 首次使用时会自动初始化 Playwright 浏览器，可能需要一些时间
- 截图功能依赖于 Playwright，确保已正确安装
- 网络请求可能会受到网络环境的影响，请确保网络连接正常

