# 配置文件说明

后端配置集中在 INI 文件中读取。默认查找顺序：

1. `DORAMI_CONFIG_FILE` 指定的文件。
2. 仓库内 `config/backend.ini`。
3. 仓库内 `config/local.ini`。
4. 代码内默认值。

仓库提供示例文件 `config/backend.example.ini`。真实部署文件可能包含管理员密码、auth secret、小鲁班凭证、图床 secret 等敏感值，已通过 `.gitignore` 排除，不应提交。

PM2 部署时建议只在 `ecosystem.config.js` 中保留配置文件路径：

```js
module.exports = {
  apps: [{
    name: 'dorami',
    script: 'src/main.py',
    interpreter: '.venv/bin/python',
    env: {
      DORAMI_CONFIG_FILE: '/opt/dorami/config/production.ini'
    }
  }]
}
```

前端配置集中在 `frontend/app.config.json`：

- `apiBaseUrl`：浏览器请求 API 的基础路径。
- `logoPath`：控制台 logo 静态资源路径。
- `devServer.port`：Vite 本地开发端口。
- `devServer.proxyTarget`：Vite `/api` 代理的后端地址。
