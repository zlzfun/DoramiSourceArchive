import { View } from '@tarojs/components';

export default function ListSkeleton({ rows = 6 }) {
  return (
    <View className="list">
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} className="entry" style={{ paddingLeft: '12px' }}>
          <View className="skeleton" style={{ height: '10px', width: '38%' }} />
          <View className="skeleton" style={{ height: '16px', width: `${[92, 78, 85, 70, 88, 76][i % 6]}%`, marginTop: '8px' }} />
          <View className="skeleton" style={{ height: '12px', width: '96%', marginTop: '8px' }} />
        </View>
      ))}
    </View>
  );
}
