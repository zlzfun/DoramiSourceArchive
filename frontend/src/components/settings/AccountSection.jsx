import { useRef, useState } from 'react';
import { KeyRound, Loader2, LogOut, Trash2, Upload } from 'lucide-react';
import { changeOwnPassword, updateAvatar } from '../../api';
import { SectionHeading, FieldRow } from './SectionPrimitives';

// 客户端把头像缩到 maxSize 见方以内并转成 JPEG data URL，控制体积（后端再做上限校验）。
function readImageAsDataUrl(file, maxSize = 256) {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith('image/')) { reject(new Error('请选择图片文件')); return; }
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('读取图片失败'));
    reader.onload = () => {
      const img = new Image();
      img.onerror = () => reject(new Error('图片解析失败'));
      img.onload = () => {
        const scale = Math.min(1, maxSize / Math.max(img.width, img.height));
        const w = Math.max(1, Math.round(img.width * scale));
        const h = Math.max(1, Math.round(img.height * scale));
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, w, h);
        resolve(canvas.toDataURL('image/jpeg', 0.85));
      };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

export default function AccountSection({ username, avatar, accountRoleLabel, onUserUpdated, onLogout, showToast }) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [saving, setSaving] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const fileInputRef = useRef(null);

  const initials = (username?.trim()?.slice(0, 2) || 'AD').toUpperCase();

  const handleAvatarFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = ''; // 允许重选同一文件
    if (!file) return;
    setAvatarBusy(true);
    try {
      const dataUrl = await readImageAsDataUrl(file);
      const result = await updateAvatar(dataUrl);
      onUserUpdated?.({ avatar: result.user?.avatar || dataUrl });
      showToast('头像已更新', 'success');
    } catch (error) {
      showToast(error.message || '更新头像失败', 'error');
    } finally {
      setAvatarBusy(false);
    }
  };

  const handleRemoveAvatar = async () => {
    setAvatarBusy(true);
    try {
      await updateAvatar('');
      onUserUpdated?.({ avatar: null });
      showToast('已移除头像', 'success');
    } catch (error) {
      showToast(error.message || '移除头像失败', 'error');
    } finally {
      setAvatarBusy(false);
    }
  };

  const handleChangePassword = async (event) => {
    event.preventDefault();
    if (!currentPassword || !newPassword) {
      showToast('请填写当前密码与新密码', 'error');
      return;
    }
    if (newPassword.length < 6) {
      showToast('新密码至少 6 位', 'error');
      return;
    }
    if (newPassword !== confirmPassword) {
      showToast('两次输入的新密码不一致', 'error');
      return;
    }
    setSaving(true);
    try {
      await changeOwnPassword(currentPassword, newPassword);
      showToast('密码已修改', 'success');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (error) {
      showToast(error.message || '修改密码失败', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <SectionHeading title="账户" />

      <div className="surface-card mb-4 flex items-center gap-4 rounded-[var(--r-card)] p-4">
        {avatar ? (
          <img src={avatar} alt="头像" className="h-16 w-16 rounded-full object-cover shadow-sm ring-1 ring-black/5" />
        ) : (
          <div className="avatar-badge flex h-16 w-16 items-center justify-center rounded-full text-base font-bold text-white">{initials}</div>
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold text-slate-700">头像</p>
          <p className="tiny-meta mt-1">支持 JPG/PNG 等图片，会自动缩为方形缩略图。</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" onClick={() => fileInputRef.current?.click()} disabled={avatarBusy} className="action-button action-button-secondary">
              {avatarBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />} 上传头像
            </button>
            {avatar && (
              <button type="button" onClick={handleRemoveAvatar} disabled={avatarBusy} className="action-button action-button-quiet">
                <Trash2 className="h-4 w-4" /> 移除
              </button>
            )}
          </div>
          <input ref={fileInputRef} type="file" accept="image/*" onChange={handleAvatarFile} className="hidden" />
        </div>
      </div>

      <div className="surface-card rounded-[var(--r-card)] px-4">
        <FieldRow label="登录账户">{username || '—'}</FieldRow>
        <FieldRow label="账户角色">{accountRoleLabel}</FieldRow>
      </div>

      <form onSubmit={handleChangePassword} className="surface-card mt-4 rounded-[var(--r-card)] p-4">
        <p className="text-sm font-bold text-slate-700">修改密码</p>
        <p className="tiny-meta mt-1">修改后当前会话仍然有效，下次登录请使用新密码。</p>
        <div className="mt-3 space-y-3">
          <input
            type="password"
            value={currentPassword}
            onChange={e => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
            placeholder="当前密码"
            className="form-input w-full"
          />
          <input
            type="password"
            value={newPassword}
            onChange={e => setNewPassword(e.target.value)}
            autoComplete="new-password"
            placeholder="新密码（至少 6 位）"
            className="form-input w-full"
          />
          <input
            type="password"
            value={confirmPassword}
            onChange={e => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
            placeholder="确认新密码"
            className="form-input w-full"
          />
        </div>
        <button type="submit" disabled={saving} className="action-button action-button-primary mt-4">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />} 保存新密码
        </button>
      </form>

      <button onClick={onLogout} className="action-button action-button-danger mt-4">
        <LogOut className="h-4 w-4" /> 退出登录
      </button>
    </div>
  );
}
