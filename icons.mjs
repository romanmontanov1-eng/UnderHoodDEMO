// Original line icons: one coordinate grid, stroke weight and optical size.
const iconPaths={
 'arrow-ne':'M5 12h12.5M12.5 6.5l4.8 4.6q.9.9 0 1.8l-4.8 4.6',
 'arrow-down':'M12 5v12.5M6.5 12.5l4.1 4.1q1.4 1.4 2.8 0l4.1-4.1',
 'arrow-up':'M12 19V6.5M6.5 11.5l4.1-4.1q1.4-1.4 2.8 0l4.1 4.1',
 'chevron-down':'M6.5 9.5l4.1 4.1q1.4 1.4 2.8 0l4.1-4.1',
 'plus':'M12 5.5v13M5.5 12h13',
 'minus':'M5.5 12h13',
 'close':'m6 6 12 12M18 6 6 18'
};
export function uiIcon(name){return '<svg class="icon icon-'+name+'" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="'+iconPaths[name]+'"/></svg>'}
export function iconizeMarkup(markup){const names={'↗':'arrow-ne','↓':'arrow-down','↑':'arrow-up','⌄':'chevron-down','＋':'plus','×':'close','−':'minus'};return markup.split(/(<[^>]+>)/g).map((part,i)=>i%2?part:part.replace(/[↗↓↑⌄＋×−]/g,char=>uiIcon(names[char]))).join('')}
