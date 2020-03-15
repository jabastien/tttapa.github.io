import re
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import guess_lexer_for_filename
from json import JSONDecoder
from pprint import pformat
from os import getenv
from os import path
from os.path import exists, splitext, dirname, relpath
import sys
from subprocess import check_output, CalledProcessError

# region Git links

def get_git_remote_link(file, startline, endline):
    """
    E.g. https://github.com/tttapa/RPi-Cpp-Toolchain/blob/acd5b556709de02fa2de5f0cd05e5d24be6f7768/applications/hello-world/hello-world.cpp#L1-L3
    """

    # First check if the directory is in a Git repository
    directory = dirname(file)
    try:
        git_dir = check_output(["git", "rev-parse", "--show-toplevel"], 
                               cwd=directory)
    except CalledProcessError:
        return None, None
    
    # Get the relative path of the file, relative to the toplevel git folder
    git_dir = git_dir.decode('utf-8').strip()
    rel = relpath(file, git_dir)

    # Get the hash of the latest commit
    commit = check_output(["git", "rev-list", "-1", "HEAD", "--", rel],
                          cwd=git_dir)
    commit = commit.decode('utf-8').strip()

    # Get the remote of the repository
    remote = check_output(["git", "remote", "get-url", "origin"],
                          cwd=directory)
    remote = remote.decode('utf-8').strip()
    if m := re.match(r'^\w+@([\w.]+):([\w/.-]+)$', remote):
        remote = 'https://' + m.group(1) + '/' + m.group(2)
    if m := re.match(r'^(.*)\.git$', remote):
        remote = m.group(1)
    m = re.match(r'^https://(\w+)\..+$', remote)
    if m is None:
        print("Warning: unknown Git remote: " + remote)
        return None, None
    service = m.group(1)
    
    # Append the commit hash and file path
    remote += '/' + 'blob' + '/' + commit + '/' + rel

    # Add the line numbers
    if startline is not None:
        remote += '#L' + str(startline)
        if endline is not None:
            if service == 'github':
                remote += '-L' + str(endline)
            else:
                remote += '-' + str(endline)
    else:
        if endline is not None:
            if service == 'github':
                remote += '#L1-L' + str(endline)
            else:
                remote += '#L1-' + str(endline)

    return service, remote

# endregion

# region Header anchors

def format_anchor_name(match, anchors):
    tag = match.group(1)
    attr = match.group(2)
    fullname = match.group(3)
    name = fullname
    name = name.lower()                       # To lowercase
    name = re.sub(r"&(?:[a-z\d]+|#\d+|#x[a-f\d]+);", r"-", name)     # Replace HTML entities with '-'
    
    name = re.sub(r"(<|</)([^<>])+>", r"", name)      # Leave out HTML tags
    name = re.sub(r"[^a-z\d]", r"-", name)    # Only alphanumeric lowercase, replace everything else with '-'
    name = re.sub(r"-+", r"-", name)          # More than one '-' replaced with single '-'
    name = re.sub(r"/^-|-$", r"", name)       # Strip '-' from the start or end of the string

    # Don't allow duplicate IDs, so add a number at the end to make it unique
    i = 1
    newname = name
    while newname in anchors:
        newname = name + str(i)
        i += 1
    anchors.add(newname)

    return "<h" + tag + attr + "><a name=\"" + newname \
         + "\" href=\"#" + newname \
         + "\">"+fullname+"</a></h" + tag + ">"

def addAnchors(html):
    anchors = set()
    html = re.sub(r"<h([1-6])([^>]*)>(((?!<a).)+)</h\1>", \
                  lambda match: format_anchor_name(match, anchors), \
                  html)
    return html

# endregion

# region Code line numbers

def addCodeLines(match):
    pre = match.group(1)
    pre = re.sub(r"(<pre.*?>)(?:\r\n|\n)*", r"\g<1><code>", pre)
    pre = re.sub(r"\r\n|\n", r"</code>\g<0><code>", pre)
    pre = re.sub(r"</pre>", r"</code></pre>", pre)
    return pre

def addLineNumbersEmphasis(match):
    pre = match.group(1)
    pre = re.sub(r"(<pre.*?>)(?:\r\n|\n)*", r"\g<1><code>", pre)
    pre = re.sub(r"\r\n|\n",r"</code>\g<0><code>", pre)
    pre = re.sub(r"</pre>", r"</code></pre>", pre)
    pre = re.sub(r"<code>(\s*)\*\*\*", r'<code class="emphasis">\g<1>', pre)
    return pre

def formatCode(html):
    findPre = r"(<pre[^>]* class=\"lineNumbers( |\")[^>]*>((?!<pre).|\r\n|\n)*</pre>)"
    findPreEmph = r"(<pre[^>]* class=\"lineNumbersEmphasis[^>]*>((?!<pre).|\r\n|\n)*</pre>)"

    html = re.sub(findPre, addCodeLines, html)
    html = re.sub(findPreEmph, addLineNumbersEmphasis, html)

    # Arduino IDE export HTML doesn't encode HTML entities
    html = re.sub(r">&<", r">&amp;<", html)
    html = re.sub(r">&=<", r">&amp;=<", html)

    return html

# endregion

# region Code syntax highlighting

def full_filepath(file, fpath):
    file = re.sub(r'\$(\w+)', lambda m: getenv(m.group(1)), file)
    file = re.sub(r'\$\{(\w+)\}', lambda m: getenv(m.group(1)), file)
    if file[0] != '/':
        file = path.dirname(fpath) + '/' + file
    return file

def clip_file_contents(file, startline, endline):
    startline = 1 if startline is None else startline
    emptylines = 0
    nonemptylineencountered = False
    filecontents = ""

    with open(file) as f:
        for i, line in enumerate(f):
            if i >= (startline - 1):
                filecontents += line
                if not nonemptylineencountered and line == '\n':
                    emptylines += 1
                else:
                    nonemptylineencountered = True
            if endline is not None and i >= (endline - 1):
                break

    ctrstart = emptylines + startline - 1
    return filecontents, ctrstart

def formatPygmentsCodeSnippet(data: dict, html, filepath, lineno):
    startline = data.get('startline', None)
    endline = data.get('endline', None)
    name = data.get('name', None)
    file = full_filepath(data['file'], filepath)

    # Get the GitHub/GitLab links for this file
    git_service, git_link = get_git_remote_link(file, startline, endline)

    # Select the lines between startline and endline
    filecontents, ctrstart = clip_file_contents(file, startline, endline)

    # Select the right lexer based on the filename and contents
    lex_filename = path.basename(file)
    if lex_filename == 'CMakeLists.txt':
        lex_filename += '.cmake'
    lexer = guess_lexer_for_filename(lex_filename, filecontents)
    
    # Select the right formatter based on the lexer
    cssclass = 'pygments{}'.format(lineno)
    if lexer.name == "Arduino" and not "style" in data:
        formatter = HtmlFormatter(cssclass=cssclass, style='arduino')
    else:
        style = data.get('style', 'default')
        formatter = HtmlFormatter(cssclass=cssclass, style=style)
    
    # Extract the CSS from the formatter, and set the line number offset
    css = formatter.get_style_defs('.' + cssclass)
    css += '\n.pygments{} pre.snippet{} {{ counter-reset: line {}; }}' \
        .format(lineno, lineno, ctrstart)
    
    # Syntax highlight the code
    htmlc = highlight(filecontents, lexer, formatter)
    
    # Set the right classes
    htmlc = htmlc.replace('<pre>', 
                '<pre class="lineNumbers snippet{}">'.format(lineno))
    htmlc = htmlc.replace('\n</pre></div>', '</pre></div>')

    # Construct the final HTML code
    datastr = ''
    if name is not None:
        datastr += '<h4 class="snippet-name">' + name + '</h4>\n'
    datastr += '<div class="codesnippet"><style>' + css + '</style>'
    if git_link is not None and git_service == 'github':
        datastr += '<a href="' + git_link + '" title="Open on GitHub">'
        datastr += '<img class="github-mark" src="/Images/GitHub-Mark.svg"/>' 
        datastr += '</a>\n'
    if git_link is not None and git_service == 'gitlab':
        datastr += '<a href="' + git_link + '" title="Open on GitLab">'
        datastr += '<img class="gitlab-mark" src="/Images/GitLab-Mark.svg"/>' 
        datastr += '</a>\n'
    datastr += htmlc + '</div>'
    return datastr, file

# endregion

# region Image linking

def formatImage(data, html, filepath, lineno):
    file = data['file']
    # file = re.sub(r'\$(\w+)', lambda m: getenv(m.group(1)), file)
    if file[0] != '/': 
        fullfile = path.dirname(filepath) + '/' + file
    else:
        raise Exception('Image file "' + file + '" cannot be an absolute path')
    if not exists(fullfile):
        raise Exception('Image file "' + fullfile + '" does not exist')

    displayfile = data.get('display-file', file)
    # file = re.sub(r'\$(\w+)', lambda m: getenv(m.group(1)), file)
    if displayfile[0] != '/': 
        fulldisplayfile = path.dirname(filepath) + '/' + displayfile
    else: 
        raise Exception('Image file "' + displayfile
                      + '" cannot be an absolute path')
    if not exists(fulldisplayfile):
        raise Exception('Image file "' + fulldisplayfile + '" does not exist')

    alt = data.get('alt', data.get('caption', file))

    htmlstr = '<a href="{src}"><img src="{dispsrc}" alt="{alt}"/></a>' \
        .format(src=file, dispsrc=displayfile, alt=alt)
        
    cap = data.get('caption')
    if cap:
        htmlstr += '<figcaption>' + cap + '</figcaption>'

    return htmlstr, fullfile

# endregion

# region Replace @ JSON tags

def getlinenumber(string: str, index: int) -> int:
    return string.count('\n', 0, index)

def getlinesbetween(string: str, startindex: int, endindex: int) -> int:
    return string.count('\n', startindex, endindex)

def getlines(string: str) -> int:
    return string.count('\n')

def getKeyWord(keywords: dict, html, index):
    for kw in keywords.keys():
        if kw + '{' == html[index:index + len(kw) + 1]:
            return kw
    return None

def replaceTags(html, filepath, lineno, outpath):
    keywordhandlers = {
        'codesnippet': formatPygmentsCodeSnippet,
        'image': formatImage,
    }

    deps = []
    index = html.find('@')
    while index >= 0:
        keyword = getKeyWord(keywordhandlers, html, index + 1)
        if not keyword:
            index = html.find('@', index + 1)
            continue
        try:
            jsonstartindex = index + len(keyword) + 1
            data, endindex = JSONDecoder().raw_decode(html, jsonstartindex)
            handler = keywordhandlers[keyword]
            taglineno = lineno + getlinenumber(html, index)
            newdata, dep = handler(data, html, filepath, taglineno)
            if dep: deps.append(dep)
            lineno += getlinesbetween(html, index, endindex)
            lineno -= getlines(newdata)
            html = html[:index] + newdata + html[endindex:]
            index = html.find('@', index + len(newdata))
        except Exception as e:
            fileline = filepath + ':' + str(lineno + getlinenumber(html, index))
            print('\nError adding code snippet:', file=sys.stderr)
            print(fileline, file=sys.stderr)
            print(type(e).__name__, file=sys.stderr)
            print(e, file=sys.stderr)
            print(file=sys.stderr)
            raise e
    if deps:
        depfname = path.splitext(outpath)[0] + '.dep'
        with open(depfname, 'w') as f:
            f.write('\n'.join(deps))
    return html

# endregion

def formatHTML(html, filepath, lineno, outpath):
    html = replaceTags(html, filepath, lineno, outpath)
    html = formatCode(html)
    html = addAnchors(html)
    return html