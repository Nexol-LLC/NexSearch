/* Run against the local preview bridge with Playwright on NODE_PATH.
 * Uses in-memory company fixtures; it never saves a company or edits a database.
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs/promises');
const {_electron} = require('playwright');
const root = path.resolve(__dirname, '../../..');

(async () => {
  const env = {...process.env};
  delete env.ELECTRON_RUN_AS_NODE;
  const app = await _electron.launch({
    executablePath: path.join(root, 'preview/HostWindows/desktop/runtime/electron.exe'),
    args: [path.join(root, 'desktop/app'), '--url=http://127.0.0.1:8898/',
      '--profile=' + path.join(root, 'preview/qa-company-card'), '--hidden'],
    env,
  });
  const page = await app.firstWindow();
  await app.evaluate(({BrowserWindow,webContents}) => {
    BrowserWindow.getAllWindows().forEach(window => window.setMinimumSize(320,600));
    webContents.getAllWebContents().forEach(contents => contents.setBackgroundThrottling(false));
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.waitForSelector('#workspace:not([hidden])', {timeout: 30000});
    console.log('Workspace ready');
    const fixture = {
      id: '00000000-0000-4000-8000-000000000001',
      name: 'ДЕМО · Уральская горно-металлургическая компания',
      inn: '9650000013', ogrn: '1234567890123', kpp: '965001001',
      verification: {okved: '24.44'},
      segment: 'Производство меди',
      legal_address: '624090, Свердловская область, г. Верхняя Пышма, проспект Успенский, дом 1',
      phones: ['+7 900 100-10-10', '+7 900 200-20-20'],
      emails: ['logistics@example.org', 'a'.repeat(100) + '@example.org'],
      director: 'Тестовый руководитель', status: 'Действующая организация',
      lead_status: 'new', call_note: '', financial: {year: 2025, revenue_rub: 123456789},
    };
    await page.evaluate(company => {
      view = 'search';
      editCompany({...company, employee_id: user.id});
    }, fixture);
    console.log('Company opened');
    const expected = [fixture.inn, fixture.ogrn, fixture.kpp, fixture.verification.okved,
      ...fixture.phones, ...fixture.emails];
    const copies = page.locator('#editor-body .detail [data-copy]');
    assert.deepEqual(await copies.evaluateAll(nodes => nodes.map(node => node.dataset.copy)), expected);
    assert.deepEqual(await copies.evaluateAll(nodes => nodes.map(node => node.childNodes[0].textContent)), expected);
    assert.equal(await page.locator('#editor-body .detail .detail').count(), 0, 'Nested fields');
    assert.equal(await page.getByText('ИНН / ОГРН', {exact: true}).count(), 0, 'Duplicate identifiers');
    assert.equal(await page.locator('#edit-note').count(), 1, 'Editor controls lost');
    for (let i = 0; i < expected.length; i++) {
      console.log('Copy',i);
      await copies.nth(i).click();
      await page.waitForFunction(value => document.querySelector('#copy-toast').textContent === 'Скопировано: ' + value, expected[i]);
      assert.equal(await app.evaluate(({clipboard}) => clipboard.readText()), expected[i]);
    }
    const report = [];
    for (const width of [1440, 1040, 520, 390]) {
      await app.evaluate(({BrowserWindow},width) => BrowserWindow.getAllWindows()[0].setContentSize(width,940),width);
      await page.setViewportSize({width, height: 940});
      await page.locator('#editor').evaluate(node => { node.scrollTop = 0; });
      const layout = await page.locator('#editor-body').evaluate(body => {
        const sphere = [...body.querySelectorAll(':scope > .detail')]
          .find(row => row.firstElementChild.textContent === 'Сфера').lastElementChild;
        const editor = document.querySelector('#editor');
        return {width: sphere.getBoundingClientRect().width, height: sphere.getBoundingClientRect().height,
          scrollWidth: editor.scrollWidth, clientWidth: editor.clientWidth};
      });
      assert(layout.width > 160 && layout.height < 60, JSON.stringify(layout));
      if(layout.scrollWidth > layout.clientWidth + 1){
        console.log('Overflow at',width,layout,await page.locator('#editor').evaluate(editor=>[...editor.querySelectorAll('*')].filter(node=>node.getBoundingClientRect().right>editor.getBoundingClientRect().right).map(node=>({tag:node.tagName,cls:node.className,text:node.textContent.slice(0,40),width:node.getBoundingClientRect().width}))));
        await page.screenshot({path:path.join(root,'preview/company-card-overflow.png'),animations:'disabled',timeout:10000});
      }
      assert(layout.scrollWidth <= layout.clientWidth + 1, 'Dialog overflows horizontally');
      report.push({viewport: width, ...layout});
      if (width === 1440) await page.screenshot({path: path.join(root, `preview/company-card-${width}.png`),animations:'disabled',timeout:10000});
    }
    await page.evaluate(() => {
      closeEditor();
      editCompany({id: '00000000-0000-4000-8000-000000000001', name: 'ДЕМО · пустые реквизиты', phones: [], emails: []});
    });
    assert.equal(await page.locator('#editor-body [data-copy]').count(), 0, 'Empty values rendered');
    assert.equal(await page.locator('#editor-body .detail .detail').count(), 0);
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(root, 'preview/company-card-report.json'), JSON.stringify(report, null, 2));
    console.log('Company fields, clipboard, empty values and layout at four widths passed.', report);
  } catch(error) {
    console.log('Page:',page.url(),await page.locator('body').innerText().catch(()=>''),'Errors:',errors);
    throw error;
  } finally {
    await app.evaluate(({BrowserWindow}) => BrowserWindow.getAllWindows().forEach(window => window.removeAllListeners('close')));
    await app.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
