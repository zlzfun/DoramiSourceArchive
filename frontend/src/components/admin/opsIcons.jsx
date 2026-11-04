// 运维信息架构(issue #114)的页图标:opsNav 注册表里只存名字,这里映射到 lucide 图标(首页磁贴页头与面包屑弹层共用)。
import { Activity, Brain, ChartColumn, HardDrive, MessageSquare, Podcast, Rss, Tags, Users } from 'lucide-react';

const ICONS = {
  users: Users,
  chart: ChartColumn,
  brain: Brain,
  podcast: Podcast,
  tags: Tags,
  'hard-drive': HardDrive,
  rss: Rss,
  message: MessageSquare,
  activity: Activity,
};

export function OpsIcon({ name, className = '' }) {
  const Icon = ICONS[name] || Activity;
  return <Icon className={className} aria-hidden="true" />;
}
