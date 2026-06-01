document.addEventListener('DOMContentLoaded', () => {
    const header = document.querySelector('.site-header');
    const menu = document.querySelector('#site-menu');
    const menuButton = document.querySelector('.menu-button');
    const navLinks = document.querySelectorAll('.site-nav a[href^="#"]');
    const revealTargets = document.querySelectorAll('.section-heading, .feature-card, .command-card, .flow-grid article, .snapshot-panel, .candidate-table, .signal-grid article');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const closeMenu = () => {
        if (!menu || !menuButton) return;
        menu.classList.remove('open');
        document.body.classList.remove('menu-open');
        menuButton.setAttribute('aria-expanded', 'false');
    };

    if (menu && menuButton) {
        menuButton.addEventListener('click', () => {
            const isOpen = menu.classList.toggle('open');
            document.body.classList.toggle('menu-open', isOpen);
            menuButton.setAttribute('aria-expanded', String(isOpen));
        });
    }

    navLinks.forEach((link) => {
        link.addEventListener('click', (event) => {
            const href = link.getAttribute('href');
            const target = href ? document.querySelector(href) : null;
            if (!target) return;

            event.preventDefault();
            closeMenu();

            const offset = header ? header.offsetHeight + 16 : 88;
            window.scrollTo({
                top: target.offsetTop - offset,
                behavior: reduceMotion ? 'auto' : 'smooth'
            });
        });
    });

    document.querySelectorAll('.copy-button').forEach((button) => {
        button.addEventListener('click', async () => {
            const value = button.getAttribute('data-copy') || '';
            try {
                await navigator.clipboard.writeText(value);
                button.textContent = 'Copied';
                button.classList.add('copied');
                window.setTimeout(() => {
                    button.textContent = 'Copy';
                    button.classList.remove('copied');
                }, 1400);
            } catch {
                button.textContent = 'Select text';
            }
        });
    });

    revealTargets.forEach((target) => target.classList.add('reveal'));

    if (reduceMotion || !('IntersectionObserver' in window)) {
        revealTargets.forEach((target) => target.classList.add('active'));
    } else {
        const revealObserver = new IntersectionObserver((entries, observer) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('active');
                    observer.unobserve(entry.target);
                }
            });
        }, { rootMargin: '0px 0px -10% 0px', threshold: 0.12 });

        revealTargets.forEach((target) => revealObserver.observe(target));
    }

    const sections = Array.from(document.querySelectorAll('main section[id]'));
    if ('IntersectionObserver' in window && sections.length > 0) {
        const navObserver = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                navLinks.forEach((link) => {
                    link.classList.toggle('active', link.getAttribute('href') === `#${entry.target.id}`);
                });
            });
        }, { rootMargin: '-42% 0px -48% 0px', threshold: 0.01 });

        sections.forEach((section) => navObserver.observe(section));
    }
});
