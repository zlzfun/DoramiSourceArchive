// 同一份数据上「修改」与「整份重取」的先后(issue #344 第二个 PR,检视 R1 #4)。
//
// 抽屉里对一个令牌的修改(绑定 / 解绑 / 权限开关 / 吊销)各自的回包都带回整个令牌对象:两个修改同时在途时,
// 后发出的那个先回来、先发出的那个后回来,旧状态就把新状态盖掉了。整表重取同理——取数期间发生过修改,
// 这份结果可能是修改之前的。约定:
//   · 修改串行:有一个在途时,别的不放行(界面上把其余控件禁用);
//   · 重取时记下当时的代次,回来时代次变了(期间有修改开始或结束)就作废:修改还在途的,等它结束后再取;
//     修改已经结束的,调用方立即再取一次(没有别人会替它补)。
export function createMutationGate() {
  let generation = 0;
  let busy = false;
  let reloadWanted = false;
  return {
    get busy() { return busy; },
    // 开始一次修改;已有修改在途时返回 false(调用方不发请求)。
    begin() {
      if (busy) return false;
      busy = true;
      generation += 1;
      return true;
    },
    // 修改结束(成功或失败都要调);返回 true 表示期间有重取被作废,调用方应当再取一次。
    end() {
      busy = false;
      generation += 1;
      const again = reloadWanted;
      reloadWanted = false;
      return again;
    },
    // 重取前记下代次。
    snapshot() { return generation; },
    // 重取回来后问:这份结果怎么处置?
    //   'use'   —— 能用;
    //   'later' —— 修改还在途:作废,修改结束时(end() 返回 true)再取;
    //   'again' —— 取数期间有过修改、现在已经结束:作废,调用方立即再取一次。
    check(snapshot) {
      if (busy) {
        reloadWanted = true;
        return 'later';
      }
      return snapshot === generation ? 'use' : 'again';
    },
  };
}
