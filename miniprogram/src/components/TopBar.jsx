import { View, Text, Input } from '@tarojs/components';

/** 条目流工具行(原生导航栏之下):源入口 + 标题/搜索 + 全部|未读 seg + 标读 + 搜索开关(mobile.css .m-topbar 的翻译;
 *  页面标题同时经 Taro.setNavigationBarTitle 写进原生导航栏)。 */
export default function TopBar({
  title, onOpenSources, searchOpen, searchValue, onSearchChange, onToggleSearch,
  unreadOnly, onUnreadOnlyChange, onMarkAllRead, markingRead, favOnly,
}) {
  return (
    <View className="topbar">
      <View className="iconbtn" onClick={onOpenSources} hoverClass="none">
        <Text>☰</Text>
      </View>
      {searchOpen ? (
        <Input
          className="search-inline"
          value={searchValue}
          placeholder="搜索我的阅读…"
          confirmType="search"
          focus
          onInput={(e) => onSearchChange(e.detail.value)}
          onConfirm={(e) => onSearchChange(e.detail.value, true)}
        />
      ) : (
        <Text className="topbar-title">{title}</Text>
      )}
      {!favOnly && !searchOpen && (
        <>
          <View className="mini-seg">
            {[[false, '全部'], [true, '未读']].map(([value, label]) => (
              <View
                key={label}
                className={`mini-seg-btn ${unreadOnly === value ? 'is-on' : ''}`}
                onClick={() => onUnreadOnlyChange(value)}
              >
                {label}
              </View>
            ))}
          </View>
          <View className={`iconbtn ${markingRead ? 'is-disabled' : ''}`} onClick={markingRead ? undefined : onMarkAllRead}>
            <Text>✓✓</Text>
          </View>
        </>
      )}
      <View className={`iconbtn ${searchOpen ? 'is-on' : ''}`} onClick={onToggleSearch}>
        <Text>{searchOpen ? '✕' : '⌕'}</Text>
      </View>
    </View>
  );
}
