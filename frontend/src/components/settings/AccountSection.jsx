import { useRef, useState } from 'react';
import { KeyRound, Loader2 } from 'lucide-react';
import { changeOwnPassword, updateAvatar } from '../../api';

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

// 账户(弹窗波,设置行范式):头像行 + 改密码行内表单(区内唯一 primary)+ 退出登录 danger 行。
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
      <div className="sett-row">
        {avatar ? (
          <img src={avatar} alt="头像" className="sett-avatar object-cover" />
        ) : (
          <span className="sett-avatar avatar-badge text-white">{initials}</span>
        )}
        <span className="sett-id">
          <span className="sett-acct-name">{username || '—'}</span>
          <div className="sett-sub"><span className="sett-role-chip">{accountRoleLabel}</span></div>
        </span>
        <span className="sett-ctl">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={avatarBusy}
            className="action-button action-button-secondary min-h-[32px] px-3 text-xs"
          >
            {avatarBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null} 更换头像
          </button>
          {avatar && (
            <button
              type="button"
              onClick={handleRemoveAvatar}
              disabled={avatarBusy}
              className="action-button action-button-quiet min-h-[32px] px-3 text-xs"
            >
              移除
            </button>
          )}
          <input ref={fileInputRef} type="file" accept="image/*" onChange={handleAvatarFile} className="hidden" />
        </span>
      </div>

      <form className="sett-row is-block" onSubmit={handleChangePassword}>
        <span className="sett-id">
          <span className="sett-lbl">修改密码</span>
          <div className="sett-sub">改密后当前会话保持有效,下次登录使用新密码</div>
        </span>
        <div className="sett-pw-grid">
          <input
            type="password"
            value={currentPassword}
            onChange={e => setCurrentPassword(e.target.value)}
            autoComplete="current-password"
            placeholder="当前密码"
            aria-label="当前密码"
            className="form-input"
          />
          <input
            type="password"
            value={newPassword}
            onChange={e => setNewPassword(e.target.value)}
            autoComplete="new-password"
            placeholder="新密码(至少 6 位)"
            aria-label="新密码"
            className="form-input"
          />
          <input
            type="password"
            value={confirmPassword}
            onChange={e => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
            placeholder="确认新密码"
            aria-label="确认新密码"
            className="form-input"
          />
          <button type="submit" disabled={saving} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />} 更新密码
          </button>
        </div>
      </form>

      <div className="sett-row">
        <span className="sett-id">
          <span className="sett-lbl">退出登录</span>
          <div className="sett-sub">仅退出本浏览器的会话</div>
        </span>
        <span className="sett-ctl">
          <button type="button" onClick={onLogout} className="action-button action-button-danger min-h-[32px] px-3 text-xs">
            退出登录
          </button>
        </span>
      </div>
    </div>
  );
}
