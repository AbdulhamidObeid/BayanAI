/* Enhance selects without changing existing value reads or change handlers. */
(() => {
  let closeCurrent = null;
  document.querySelectorAll('select').forEach(select => {
    const wrapper = document.createElement('div');
    wrapper.className = 'bayan-select';
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'bayan-select-trigger';
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    const label = select.closest('.ctrl-group')?.querySelector('.ctrl-label');
    if (label) trigger.setAttribute('aria-labelledby', label.id);
    else trigger.setAttribute('aria-label', select.getAttribute('aria-label') || 'الصفة');
    const menu = document.createElement('div');
    menu.className = 'bayan-select-menu';
    menu.id = `${select.id}-menu`;
    menu.setAttribute('role', 'listbox');
    if (label) menu.setAttribute('aria-labelledby', label.id);
    else menu.setAttribute('aria-label', select.getAttribute('aria-label') || 'الصفة');
    trigger.setAttribute('aria-controls', menu.id);
    menu.hidden = true;
    select.before(wrapper);
    wrapper.append(trigger, menu, select);
    select.classList.add('bayan-native-select');
    select.tabIndex = -1;
    if (select.classList.contains('lang-sel')) wrapper.style.minWidth = '130px';
    let active = 0;
    let search = '';
    let searchedAt = 0;
    const options = () => Array.from(select.options);
    const close = () => {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      trigger.removeAttribute('aria-activedescendant');
      if (closeCurrent === close) closeCurrent = null;
    };
    const highlight = index => {
      active = index;
      Array.from(menu.children).forEach((item, i) => item.classList.toggle('is-active', i === active));
      if (!menu.hidden && menu.children[active]) {
        trigger.setAttribute('aria-activedescendant', menu.children[active].id);
        menu.children[active].scrollIntoView({block: 'nearest'});
      }
    };
    const sync = () => {
      if (!label && select.hasAttribute('aria-label')) {
        trigger.setAttribute('aria-label', select.getAttribute('aria-label'));
        menu.setAttribute('aria-label', select.getAttribute('aria-label'));
      }
      trigger.textContent = select.selectedOptions[0]?.textContent || '';
      trigger.disabled = select.disabled;
      menu.replaceChildren(...options().map((option, i) => {
        const item = document.createElement('div');
        item.className = 'bayan-select-option';
        item.tabIndex = -1;
        item.addEventListener('mousedown', event => event.preventDefault());
        item.id = `${select.id}-option-${i}`;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', String(option.selected));
        item.setAttribute('aria-disabled', String(option.disabled));
        item.textContent = option.textContent;
        item.addEventListener('click', () => choose(i));
        return item;
      }));
      highlight(Math.max(0, select.selectedIndex));
    };
    const choose = index => {
      if (!options()[index] || options()[index].disabled) return;
      select.selectedIndex = index;
      select.dispatchEvent(new Event('input', {bubbles: true}));
      select.dispatchEvent(new Event('change', {bubbles: true}));
      sync(); close(); trigger.focus();
    };
    const open = () => {
      if (select.disabled) return;
      closeCurrent?.();
      sync(); menu.hidden = false;
      const rect = trigger.getBoundingClientRect();
      const below = innerHeight - rect.bottom - 20;
      const above = rect.top - 20;
      const opensAbove = below < Math.min(300, menu.scrollHeight) && above > below;
      wrapper.dataset.above = String(opensAbove);
      menu.style.maxHeight = `${Math.max(80, Math.min(300, opensAbove ? above : below))}px`;
      trigger.setAttribute('aria-expanded', 'true');
      closeCurrent = close; highlight(Math.max(0, select.selectedIndex));
    };
    trigger.addEventListener('click', () => menu.hidden ? open() : close());
    trigger.addEventListener('keydown', event => {
      const opts = options();
      const allowed = opts.map((option, i) => option.disabled ? -1 : i).filter(i => i >= 0);
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        if (menu.hidden) open();
        const current = allowed.indexOf(active);
        const next = event.key === 'Home' ? allowed[0] : event.key === 'End' ? allowed.at(-1) :
          allowed[Math.max(0, Math.min(allowed.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1)))];
        if (next !== undefined) highlight(next);
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault(); menu.hidden ? open() : choose(active);
      } else if (event.key === 'Escape') {
        event.preventDefault(); close();
      } else if (event.key === 'Tab') close();
      else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        const now = Date.now();
        search = now - searchedAt > 700 ? event.key : search + event.key;
        searchedAt = now;
        if (menu.hidden) open();
        const match = allowed.find(i => opts[i].textContent.trim().toLocaleLowerCase().startsWith(search.toLocaleLowerCase()));
        if (match !== undefined) highlight(match);
      }
    });
    document.addEventListener('pointerdown', event => { if (!wrapper.contains(event.target)) close(); });
    wrapper.addEventListener('focusout', event => { if (!wrapper.contains(event.relatedTarget)) close(); });
    select.addEventListener('change', sync);
    new MutationObserver(sync).observe(select, {subtree: true, childList: true, characterData: true, attributes: true});
    sync();
  });
  window.addEventListener('resize', () => closeCurrent?.());
})();
