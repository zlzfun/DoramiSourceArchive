import { useState, useEffect, useRef } from 'react';
import { Calendar, ChevronLeft, ChevronRight, X } from 'lucide-react';

function parseDateStr(dateStr) {
  if (!dateStr) return null;
  const [y, m, d] = dateStr.split('-');
  return new Date(parseInt(y, 10), parseInt(m, 10) - 1, parseInt(d, 10));
}

function formatDate(date) {
  if (!date) return '';
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd}`;
}

function isDateEqual(d1, d2) {
  return d1 && d2 && d1.getFullYear() === d2.getFullYear() && d1.getMonth() === d2.getMonth() && d1.getDate() === d2.getDate();
}

function isDateBetween(target, start, end) {
  return target && start && end && target > start && target < end;
}

export default function DateRangePicker({ startDate, endDate, onChange, placeholder = '选择起止时间' }) {
  const [isOpen, setIsOpen] = useState(false);
  const [viewDate, setViewDate] = useState(new Date());
  const [tempStart, setTempStart] = useState(null);
  const [tempEnd, setTempEnd] = useState(null);
  const [hoverDate, setHoverDate] = useState(null);
  const popoverRef = useRef();

  useEffect(() => {
    setTempStart(parseDateStr(startDate));
    setTempEnd(parseDateStr(endDate));
    if (startDate && !isOpen) {
      setViewDate(parseDateStr(startDate));
    }
  }, [startDate, endDate, isOpen]);

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (popoverRef.current && !popoverRef.current.contains(event.target)) {
        setIsOpen(false);
        setTempStart(parseDateStr(startDate));
        setTempEnd(parseDateStr(endDate));
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [startDate, endDate]);

  const year = viewDate.getFullYear();
  const month = viewDate.getMonth();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const firstDay = new Date(year, month, 1).getDay();

  const days = [];
  for (let i = 0; i < firstDay; i++) days.push(null);
  for (let i = 1; i <= daysInMonth; i++) days.push(i);

  const handleDayClick = (day) => {
    if (!day) return;
    const clickedDate = new Date(year, month, day);
    if (!tempStart || (tempStart && tempEnd)) {
      setTempStart(clickedDate);
      setTempEnd(null);
    } else {
      if (clickedDate < tempStart) {
        setTempStart(clickedDate);
      } else {
        setTempEnd(clickedDate);
        setIsOpen(false);
        onChange(formatDate(tempStart), formatDate(clickedDate));
      }
    }
  };

  let displayStr = placeholder;
  if (startDate && endDate) {
    displayStr = startDate === endDate ? startDate : `${startDate.slice(5)} ~ ${endDate.slice(5)}`;
  } else if (startDate) {
    displayStr = `${startDate.slice(5)} ~ 结束点`;
  }

  return (
    <div className="relative w-full" ref={popoverRef}>
      <div
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center justify-between bg-slate-100/70 hover:bg-slate-200/70 rounded px-2.5 py-1.5 cursor-pointer w-full transition-colors border shadow-sm ${isOpen ? 'border-blue-400 bg-blue-50/50' : 'border-transparent hover:border-slate-300'}`}
      >
        <span className={`text-[11px] font-bold truncate ${startDate ? 'text-blue-700' : 'text-slate-400'}`}>
          {displayStr}
        </span>
        {startDate ? (
          <X className="w-3.5 h-3.5 text-slate-400 hover:text-red-500 transition-colors" onClick={(e) => { e.stopPropagation(); onChange('', ''); setTempStart(null); setTempEnd(null); }} />
        ) : (
          <Calendar className={`w-3.5 h-3.5 ${isOpen ? 'text-blue-500' : 'text-slate-400'}`} />
        )}
      </div>

      {isOpen && (
        <div className="absolute top-full left-0 mt-2 bg-white border border-slate-200 rounded-2xl shadow-xl z-50 p-4 w-[260px] animate-in fade-in zoom-in-95 origin-top-left">
          <div className="flex justify-between items-center mb-4 px-1">
            <button onClick={() => setViewDate(new Date(year, month - 1, 1))} className="p-1 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"><ChevronLeft className="w-4 h-4" /></button>
            <span className="text-sm font-extrabold text-slate-800 tracking-wide">{year}年 {month + 1}月</span>
            <button onClick={() => setViewDate(new Date(year, month + 1, 1))} className="p-1 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"><ChevronRight className="w-4 h-4" /></button>
          </div>
          <div className="grid grid-cols-7 gap-y-2 mb-2">
            {['日', '一', '二', '三', '四', '五', '六'].map(d => (
              <div key={d} className="text-[10px] font-bold text-slate-400 text-center">{d}</div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-y-1" onMouseLeave={() => setHoverDate(null)}>
            {days.map((day, idx) => {
              if (!day) return <div key={idx} className="h-8 w-full"></div>;
              const currentDate = new Date(year, month, day);
              const isStart = isDateEqual(currentDate, tempStart);
              const isEnd = isDateEqual(currentDate, tempEnd);
              const inRange = isDateBetween(currentDate, tempStart, tempEnd || hoverDate);
              const hasRangeForward = tempEnd || (hoverDate && hoverDate > tempStart);

              let cellClass = 'w-full h-8 flex items-center justify-center transition-colors';
              let textClass = 'w-7 h-7 flex items-center justify-center rounded-lg text-[11.5px] font-medium cursor-pointer transition-all';

              if (isStart && isEnd) {
                textClass += ' bg-blue-600 text-white font-bold shadow-md';
              } else if (isStart) {
                cellClass += hasRangeForward ? ' bg-blue-50 rounded-l-xl' : '';
                textClass += ' bg-blue-600 text-white font-bold shadow-md scale-105';
              } else if (isEnd) {
                cellClass += ' bg-blue-50 rounded-r-xl';
                textClass += ' bg-blue-600 text-white font-bold shadow-md scale-105';
              } else if (inRange) {
                cellClass += ' bg-blue-50';
                textClass += ' text-blue-700 font-bold';
              } else {
                textClass += ' text-slate-700 hover:bg-slate-100';
              }

              return (
                <div key={idx} className={cellClass} onMouseEnter={() => tempStart && !tempEnd && setHoverDate(currentDate)}>
                  <div onClick={() => handleDayClick(day)} className={textClass}>
                    {day}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
