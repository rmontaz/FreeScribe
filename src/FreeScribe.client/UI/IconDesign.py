"""FreeScribe's 24-unit optical icon system: rounded strokes, open silhouettes.

All symbols are code-native and scale with the application's existing renderer.
"""

def design(name):
    def paint(p):
        def line(*xy): p.line(*xy)
        def box(a,b,c,d,r=2): p.rrect(a,b,c,d,r)
        def circle(x,y,r): p.circle(x,y,r)
        def arrow_up(): line((7,10),(12,5),(17,10)); line((12,5),(12,17))
        # Documents share a folded corner; actions retain distinct silhouettes.
        if name in ('doc','form','clipboard'):
            if name=='clipboard':
                box(5,5,19,21);box(9,2,15,7,1.5)
            else:
                p.line((5,3),(14,3),(19,8),(19,21),(5,21),closed=True)
                line((14,3),(14,8),(19,8))
            if name=='form': line((8,15),(11,18),(16,12))
            else: line((8,12),(15,12));line((8,16),(13,16))
        elif name=='mic':
            box(8,2.5,16,14,4)
            line((10.5,6),(13.5,6));line((10.5,9),(13.5,9))
            p.arc(12,11.5,7,0,180)
            line((12,18.5),(12,22));line((8,22),(16,22))
        elif name=='stop': box(5,5,19,19,3)
        elif name=='pause': box(5.5,4,9.5,20,1);box(14.5,4,18.5,20,1)
        elif name=='play': p.line((7,3.5),(20,12),(7,20.5),closed=True)
        elif name=='record': circle(12,12,8);p.circle(12,12,3,fill=True)
        elif name=='plus': line((12,4),(12,20));line((4,12),(20,12))
        elif name=='search': circle(10,10,6.5);line((15,15),(21,21))
        elif name=='settings':
            for y,x in ((5,8),(12,16),(19,10)):
                line((3,y),(x-3,y));line((x+3,y),(21,y));circle(x,y,2.5)
        elif name in ('help','info'):
            circle(12,12,9)
            if name=='help':
                p.arc(12,9,3,180,50);line((14,11.3),(12,13),(12,14))
                p.dot(12,18,.8)
            else: p.dot(12,7,.9);line((11,11),(12,11),(12,17),(14,17))
        elif name=='copy': box(3,3,15,16,2);p.erase(7,7,23,23);box(8,8,21,21,2)
        elif name=='check': line((3,12),(9,18),(21,5))
        elif name=='close': line((5,5),(19,19));line((19,5),(5,19))
        elif name.startswith('chevron-'):
            paths={'down':((5,8),(12,15),(19,8)),'up':((5,16),(12,9),(19,16)),
                   'right':((8,5),(15,12),(8,19)),'left':((16,5),(9,12),(16,19))}
            line(*paths[name.split('-')[1]])
        elif name=='trash':
            line((3,6),(21,6));box(8,2,16,6,1);line((5,6),(6,21),(18,21),(19,6));line((10,10),(10,17));line((14,10),(14,17))
        elif name in ('upload','download'):
            line((3,15),(3,21),(21,21),(21,15))
            if name=='upload': arrow_up()
            else: line((12,3),(12,16));line((7,11),(12,16),(17,11))
        elif name=='edit':
            p.line((4,16),(16,4),(20,8),(8,20),(3,21),closed=True);line((13,7),(17,11))
        elif name=='sparkle':
            p.line((12,2),(15,9),(22,12),(15,15),(12,22),(9,15),(2,12),(9,9),closed=True)
        elif name=='send':
            p.line((3,4),(22,12),(3,20),(7,12),closed=True);line((7,12),(15,12))
        elif name=='dots':
            for x in (4,12,20):circle(x,12,1)
        elif name in ('panel','compact','expand'):
            if name=='panel': box(2,3,22,21);line((9,3),(9,21))
            elif name=='compact': box(2,4,22,20);box(12,11,20,18,1)
            else:
                line((3,9),(3,3),(9,3));line((15,3),(21,3),(21,9));line((21,15),(21,21),(15,21));line((9,21),(3,21),(3,15))
        elif name=='clock':circle(12,12,9);line((12,6),(12,12),(16,14))
        elif name=='calendar':
            box(3,5,21,21);line((3,10),(21,10));line((8,2),(8,7));line((16,2),(16,7));p.dot(8,14,.8);p.dot(12,14,.8);p.dot(16,14,.8);p.dot(8,18,.8)
        elif name=='globe':circle(12,12,9);p.ellipse(12,12,4,9);line((3,12),(21,12))
        elif name=='user':circle(12,7,4);p.arc(12,21,8,180,360);line((4,21),(20,21))
        elif name=='refresh':p.arc(12,12,8,35,315);line((18,3),(18,8),(13,8))
        elif name=='lines':
            for x,y0,y1 in ((4,9,15),(8,5,19),(12,2,22),(16,6,18),(20,9,15)):line((x,y0),(x,y1))
        elif name=='lock':box(4,10,20,22);p.arc(12,9,5,180,360);line((7,9),(7,10));line((17,9),(17,10));p.dot(12,15,1);line((12,15),(12,18))
        elif name=='warning':p.line((12,2),(23,21),(1,21),closed=True);line((12,8),(12,13));p.dot(12,17,1)
        elif name=='folder':p.line((2,5),(9,5),(12,8),(22,8),(20,21),(2,21),closed=True);line((3,11),(21,11))
        elif name=='undo':line((7,3),(2,8),(7,13));line((2,8),(14,8));p.arc(14,14,6,270,90);line((14,20),(10,20))
        elif name=='chat':p.line((5,3),(19,3),(22,6),(22,15),(19,18),(10,18),(4,22),(4,18),(2,15),(2,6),closed=True)
        elif name=='database':
            for y in (5,11,17):p.line((3,y),(12,y-3),(21,y),(12,y+3),closed=True)
        elif name=='moon':
            from math import atan2, acos, cos, sin, hypot, pi
            (x1,y1,r1),(x2,y2,r2)=(12,12,9),(17.5,7.5,7.5)
            d=hypot(x2-x1,y2-y1); base=atan2(y2-y1,x2-x1); spread=acos((r1*r1+d*d-r2*r2)/(2*r1*d))
            outer=[base+spread+(2*pi-2*spread)*i/24 for i in range(25)]          # the long way round the big circle
            pts=[(x1+r1*cos(a),y1+r1*sin(a)) for a in outer]
            a0=atan2(pts[-1][1]-y2,pts[-1][0]-x2); a1=atan2(pts[0][1]-y2,pts[0][0]-x2)
            while a1>a0: a1-=2*pi
            pts+=[(x2+r2*cos(a0+(a1-a0)*i/16),y2+r2*sin(a0+(a1-a0)*i/16)) for i in range(1,16)]
            p.line(*pts,closed=True)
        elif name=='sun':
            circle(12,12,4)
            for a,b,c,d in ((12,2,12,5),(12,19,12,22),(2,12,5,12),(19,12,22,12),(5,5,7,7),(17,17,19,19),(19,5,17,7),(7,17,5,19)): line((a,b),(c,d))
        elif name=='translate':
            line((3,8),(20,8));line((16,4),(20,8),(16,12));line((21,16),(4,16));line((8,12),(4,16),(8,20))
        elif name=='mapper':box(3,3,21,21);line((3,9),(21,9));line((9,9),(9,21));line((12,13),(18,13));line((12,17),(16,17))
        else: raise KeyError(name)
    return paint

NAMES = ('moon sun chat form mapper translate database mic stop pause play record plus search settings help info copy check close '
         'chevron-down chevron-up chevron-right chevron-left trash upload download edit doc sparkle send dots '
         'panel compact expand clock calendar globe user refresh lines clipboard lock warning folder undo').split()
ICONS = {name: design(name) for name in NAMES}
