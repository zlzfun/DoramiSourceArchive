import { Component } from 'react';
import { CircleAlert, RefreshCw } from 'lucide-react';

// Tab 级错误边界:懒加载 chunk 拉取失败或子树渲染抛错时,把白屏收敛为本页内的
// 降级提示,不波及其它已挂载 Tab。渲染类错误重置边界即可恢复;chunk 加载失败时
// React.lazy 会缓存 rejection,重置无效,故第二次重试直接整页刷新拉新资源。
export default class TabErrorBoundary extends Component {
  state = { error: null, retried: false };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('[TabErrorBoundary]', error, info?.componentStack);
  }

  handleRetry = () => {
    if (this.state.retried) {
      window.location.reload();
      return;
    }
    this.setState({ error: null, retried: true });
  };

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3 px-6 text-center">
          <CircleAlert className="h-7 w-7 text-slate-300" />
          <div className="font-semibold text-slate-500">页面加载失败</div>
          <div className="text-slate-500">
            {this.state.retried ? '重试仍未成功,刷新页面可重新拉取资源' : '可能是网络波动或资源更新,重试通常可恢复'}
          </div>
          <button onClick={this.handleRetry} className="action-button action-button-secondary mt-2 min-h-[36px] px-5">
            <RefreshCw />
            {this.state.retried ? '刷新页面' : '重试'}
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
