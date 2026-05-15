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
    if (!isOpen) {
      setTempStart(parseDateStr(startDate));
      setTempEnd(parseDateStr(endDate));
      if (startDate) setViewDate(parseDateStr(startDate));
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
      setViewDate(new Date(clickedDate.getFullYear(), clickedDate.getMonth(), 1));
    } else {
      if (clickedDate < tempStart) {
        setTempStart(clickedDate);
        setViewDate(new Date(clickedDate.getFullYear(), clickedDate.getMonth(), 1));
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

  const toggleOpen = () => {
    if (isOpen) {
      setIsOpen(false);
      setTempStart(parseDateStr(startDate));
      setTempEnd(parseDateStr(endDate));
      return;
    }
    const parsedStart = parseDateStr(startDate);
    const parsedEnd = parseDateStr(endDate);
    setTempStart(parsedStart);
    setTempEnd(parsedEnd);
    setHoverDate(null);
    setViewDate(parsedStart || parsedEnd || new Date());
    setIsOpen(true);
  };

  return (
    <div className="relative w-full" ref={popoverRef}>
      <div
        onClick={toggleOpen}
        className={`date-range-trigger flex items-center justify-between bg-white/80 hover:bg-white rounded-lg px-3 py-3 cursor-pointer w-full transition-colors border ${isOpen ? 'border-blue-400 bg-blue-50/50 shadow-sm' : 'border-slate-200 hover:border-slate-300'}`}
      >
        <span className={`truncate text-sm font-semibold ${startDate ? 'text-blue-700' : 'text-slate-400'}`}>
          {displayStr}
        </span>
        {startDate ? (
          <X className="w-3.5 h-3.5 text-slate-400 hover:text-red-500 transition-colors" onClick={(e) => { e.stopPropagation(); onChange('', ''); setTempStart(null); setTempEnd(null); }} />
        ) : (
          <Calendar className={`w-3.5 h-3.5 ${isOpen ? 'text-blue-500' : 'text-slate-400'}`} />
        )}
      </div>

      {isOpen && (
        <div className="date-range-popover animate-in fade-in zoom-in-95">
          <div className="flex justify-between items-center mb-4 px-1">
            <button onClick={() => setViewDate(new Date(year, month - 1, 1))} className="p-1 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"><ChevronLeft className="w-4 h-4" /></button>
            <span className="date-range-month">{year}年 {month + 1}月</span>
            <button onClick={() => setViewDate(new Date(year, month + 1, 1))} className="p-1 hover:bg-slate-100 rounded-lg text-slate-500 transition-colors"><ChevronRight className="w-4 h-4" /></button>
          </div>
          <div className="grid grid-cols-7 gap-y-2 mb-2">
            {['日', '一', '二', '三', '四', '五', '六'].map(d => (
              <div key={d} className="date-range-weekday">{d}</div>
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
              let textClass = 'date-range-day';

              if (isStart && isEnd) {
                textClass += ' date-range-day-selected';
              } else if (isStart) {
                cellClass += hasRangeForward ? ' bg-blue-50 rounded-l-xl' : '';
                textClass += ' date-range-day-selected';
              } else if (isEnd) {
                cellClass += ' bg-blue-50 rounded-r-xl';
                textClass += ' date-range-day-selected';
              } else if (inRange) {
                cellClass += ' bg-blue-50';
                textClass += ' date-range-day-inrange';
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
