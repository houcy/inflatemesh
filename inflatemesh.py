from __future__ import division
from inflateutils.surface import *
import svgpath.shader as shader
import svgpath.parser as parser
import sys
import getopt
from inflateutils.exportmesh import *

quiet = False

def rasterizePolygon(polygon, spacing, shadeMode=shader.Shader.MODE_EVEN_ODD, hex=False):
    """
    Returns boolean raster of strict interior as well as coordinates of lower-left corner.
    """
    bottom = min(min(l[0].imag,l[1].imag) for l in polygon)
    left = min(min(l[0].real,l[1].real) for l in polygon)
    top = max(max(l[0].imag,l[1].imag) for l in polygon)
    right = max(max(l[0].real,l[1].real) for l in polygon)

    if hex:
        meshData = HexMeshData(right-left,top-bottom,Vector(left,bottom),spacing)
    else:
        meshData = RectMeshData(right-left,top-bottom,Vector(left,bottom),spacing)
        
    # TODO: not really optimal but simple -- the wraparound is the inoptimality
    phases = [-math.pi] + list(sorted( cmath.phase(l[1]-l[0]) for l in polygon if l[1] != l[0] ) ) + [math.pi]
    bestSpacing = 0
    bestPhase = 0.
    for i in range(1,len(phases)):
        if phases[i]-phases[i-1] > bestSpacing:
            bestPhase = 0.5 * (phases[i] + phases[i-1])
            bestSpacing = phases[i]-phases[i-1]
            
    rotate = cmath.exp(-1j * bestPhase)
    
    lines = tuple((l[0] * rotate, l[1] * rotate) for l in polygon)
    
    for col in range(meshData.cols):
        for row in range(meshData.rows):
            z = meshData.getCoordinates(col,row).toComplex() * rotate
            sum = 0
            for l in lines:
                a = l[0] - z
                b = l[1] - z
                if a.imag < 0 < b.imag or b.imag < 0 < a.imag:
                    mInv = (b.real-a.real)/(b.imag-a.imag)
                    if -a.imag * mInv + a.real >= 0:
                        if shadeMode == shader.Shader.MODE_EVEN_ODD:
                            sum += 1
                        else:
                            if a.imag < 0:
                                sum += 1
                            else:
                                sum -= 1
            if (shadeMode == shader.Shader.MODE_EVEN_ODD and sum % 2) or (shadeMode != shader.Shader.MODE_EVEN_ODD and sum != 0):
                meshData.mask[col][row] = True
                
    return meshData
    
def message(string):
    if not quiet:
        sys.stderr.write(string + "\n")
    
def inflatePolygon(polygon, spacing=1., shadeMode=shader.Shader.MODE_EVEN_ODD, inflationParams=None,
        center=False, twoSided=False, color=None, trim=True):
    # polygon is described by list of (start,stop) pairs, where start and stop are complex numbers
    message("Rasterizing")
    meshData = rasterizePolygon(polygon, spacing, shadeMode=shadeMode, hex=inflationParams.hex)
    
    def distanceToEdge(z0, direction):
        direction = direction / abs(direction)
        rotate = 1. / direction
        
        class State(object): pass
        state = State()
        state.changed = False
        state.bestLength = float("inf")
        
        for line in polygon:
            def update(x):
                if 0 <= x < state.bestLength:
                    state.changed = True
                    state.bestLength = x
        
            l0 = rotate * (line[0]-z0)
            l1 = rotate * (line[1]-z0)
            if l0.imag == l1.imag and l0.imag == 0.:
                if l0.real <= 0 and l1.real >= 0:
                    return start
                update(l0.real)
                update(l1.real)
            elif l0.imag <= 0 <= l1.imag or l1.imag <= 0 <= l0.imag:
                # crosses real line
                mInv = (l1.real-l0.real)/(l1.imag-l0.imag)
                # (x - l0.real) / mInv = y - l0.imag
                # so for y = 0: 
                x = -l0.imag * mInv + l0.real
                update(x)
        return state.bestLength

    message("Making edge distance map")
    deltas = meshData.normalizedDeltas
    deltasComplex = tuple( v.toComplex() for v in deltas )
    deltaLengths = tuple( abs(d) for d in deltasComplex )
    map = [[[1. for i in range(len(deltas))] for row in range(meshData.rows)] for col in range(meshData.cols)]
    
    for col in range(meshData.cols):
        for row in range(meshData.rows):
            v = meshData.getCoordinates(col,row)

            for i in range(len(deltasComplex)):
                map[col][row][i] = distanceToEdge( v.toComplex(), deltasComplex[i] )
            
    message("Inflating")
    
    def distanceFunction(col, row, i, map=map):
        return map[col][row][i]
    
    inflateRaster(meshData, inflationParams=inflationParams, distanceToEdge=distanceFunction)
    message("Meshing")
   
    mesh0 = meshData.getMesh(twoSided=twoSided, color=color)
    
    def fixFace(face, polygon, trim=True):
        if not trim:
            return [face]

        # TODO: optimize by using cached data from the distance map
        def trimLine(start, stop):
            delta = (stop - start).toComplex() # projects to 2D
            if delta == 0j:
                return stop
            length = abs(delta)
            z0 = start.toComplex()
            distance = distanceToEdge(z0, delta)
            if distance < length:
                z = z0 + distance * delta / length
                return Vector(z.real, z.imag, 0)
            else:
                return stop
    
        outsideCount = sum(1 for v in face if not meshData.insideCoordinates(v))
        if outsideCount == 3:
            return []
        elif outsideCount == 0:
            return [face]
        elif outsideCount == 2:
            if meshData.insideCoordinates(face[1]):
                face = (face[1], face[2], face[0])
            elif meshData.insideCoordinates(face[2]):
                face = (face[2], face[0], face[1])
            # now, the first vertex is inside and the others are outside
            return [ (face[0], trimLine(face[0], face[1]), trimLine(face[0], face[2])) ]
        else: # outsideCount == 1
            if not meshData.insideCoordinates(face[0]):
                face = (face[1], face[2], face[0])
            elif not meshData.insideCoordinates(face[1]):
                face = (face[2], face[0], face[1])
            # now, the first two vertices are inside, and the third is outside
            closest0 = trimLine(face[0], face[2])
            closest1 = trimLine(face[1], face[2])
            if closest0 != closest1:
                return [ (face[0], face[1], closest0), (closest0, face[1], closest1) ]
            else:
                return [ (face[0], face[1], closest0) ]

    message("Fixing outer faces")
    mesh = []
    for rgb,face in mesh0:
        for face2 in fixFace(face, polygon, trim=trim):
            mesh.append((rgb, face2))
            
    return mesh
    
def sortedApproximatePaths(paths,error=0.1):
    paths = [path.linearApproximation(error=error) for path in paths if len(path)]
    
    def key(path):
        top = min(min(line.start.imag,line.end.imag) for line in path)
        left = min(min(line.start.real,line.end.real) for line in path)
        return (top,left)
        
    return sorted(paths, key=key)

def inflateLinearPath(path, spacing=1., inflationParams=None, ignoreColor=False):
    lines = []
    for line in path:
        lines.append((line.start,line.end))
    mode = shader.Shader.MODE_NONZERO if path.svgState.fillRule == 'nonzero' else shader.Shader.MODE_EVEN_ODD
    return inflatePolygon(lines, spacing=spacing, inflationParams=inflationParams, twoSided=twoSided, color=None if ignoreColor else path.svgState.fill, shadeMode=mode, trim=trim) 

class InflatedData(object):
    pass
                
def inflateSVGFile(svgFile, spacing=1., inflationParams=None, twoSided=False, trim=True, ignoreColor=False, inflate=True, baseName="path"):
    data = InflatedData()
    data.meshes = []
    data.pointLists = []
    data.uninflatedPointLists = []

    paths = sortedApproximatePaths( parser.getPathsFromSVGFile(svgFile)[0], error=spacing*0.1 )
    
    for i,path in enumerate(paths):
        inflateThis = inflate and path.svgState.fill is not None
        if inflateThis:
            mesh = inflateLinearPath(path, spacing=spacing, inflationParams=inflationParams, ignoreColor=ignoreColor)
            data.meshes.append( ("inflated_" + baseName + "_" + str(i), mesh) )
        for j,subpath in enumerate(path.breakup()):
            points = [subpath[0].start]
            for line in subpath:
                points.append(line.end)
            if subpath.closed and points[0] != points[-1]:
                points.append(points[0])
            data.pointLists.append(( "points_" + baseName + "_" + str(i) + "_" + str(j), points) )
            if not inflateThis:
                data.uninflatedPointLists.append(data.pointsLists[-1])

    return data
    
if __name__ == '__main__':
    import cmath
    
    params = InflationParams()
    spacing = 1.
    output = "stl"
    width = 0.5 # TODO
    twoSided = False
    trim = True
    outfile = None
    inflate = True
    baseName = "path"
    
    def help(exitCode=0):
        help = """python inflate.py [options] filename.svg"""
        if exitCode:
            sys.stderr.write(help + "\n")
        else:
            print(help)
        sys.exit(exitCode)
    
    try:
        opts, args = getopt.getopt(sys.argv[1:], "h", 
                        ["tab=", "help", "stl", "rectangular", "mesh=", "flatness=", "name=", "thickness=", 
                        "exponent=", "resolution=", "format=", "iterations=", "width=", "xtwo-sided=", "two-sided", "output=", "trim=", "no-inflate", "xinflate="])
        # TODO: support width for ribbon-thin stuff

        if len(args) == 0:
            raise getopt.GetoptError("invalid commandline")

        i = 0
        while i < len(opts):
            opt,arg = opts[i]
            if opt in ('-h', '--help'):
                help()
                sys.exit(0)
            elif opt == '--flatness':
                params.flatness = float(arg)
            elif opt == '--thickness':
                params.thickness = float(arg)
            elif opt == '--resolution':
                spacing = float(arg)
            elif opt == '--rectangular':
                params.hex = False
            elif opt == '--mesh':
                params.hex = arg.lower()[0] == 'h'
            elif opt == '--format' or opt == "--tab":
                if opt == "--tab":
                    quiet = True
                format = arg.replace('"','').replace("'","")
            elif opt == "--stl":
                format = "stl"
            elif opt == '--iterations':
                params.iterations = int(arg)
            elif opt == '--width':
                width = float(arg)
            elif opt == '--xtwo-sided':
                twoSided = (arg == "true" or arg == "1")
            elif opt == '--xinflate':
                inflate = bool(int(arg))
            elif opt == '--no-inflate':
                inflate = False
            elif opt == '--two-sided':
                twoSided = True
            elif opt == "--name":
                baseName = arg
            elif opt == "--exponent":
                params.exponent = float(arg)
            elif opt == "--trim":
                trim = bool(int(arg))
            elif opt == "--output":
                outfile = arg
                
            i += 1
                
    except getopt.GetoptError as e:
        sys.stderr.write(str(e)+"\n")
        help(exitCode=1)
        sys.exit(2)
        
    if twoSided:
        params.thickness *= 0.5
        
    data = inflateSVGFile(args[0], inflationParams=params, spacing=spacing, twoSided=twoSided, trim=trim, inflate=inflate, baseName=baseName)
    
    if format == 'stl':
        mesh = [datum for name,mesh in data.meshes for datum in mesh]
        saveSTL(outfile, mesh, quiet=quiet)
    else:
        if data.uninflatedPointLists:
            scad = "polygonHeight = 1;\n\n"
        
        for name,points in data.pointLists:
            scad += name + " = [ " + ','.join('[%.9f,%.9f]' % (point.real,point.imag) for point in points) + " ];\n"
            
        scad += "\n";
        
        for name,mesh in data.meshes:
            scad += toSCADModule(mesh, moduleName=name)
            scad += "\n"
        
        for name,_ in data.meshes:
            scad += name + "();\n"
            
        if data.uninflatedPointLists:
            scad += "module polygon_%s() {\n" % baseName
            scad += "  linear_extrude(height=polygonHeight) {\n";
            for name,points in data.uninflatedPointLists:
                if points[0] == points[-1]:
                    scad += "  polygon(points="+name+");\n";
                else:
                    "// "+name+" is not closed\n"
            scad += "  }\n"
            scad += "}\n"
            
            scad += "polygon_%s();\n" % baseName
            
        if outfile:
            with open(outfile, "w") as f: f.write(scad)
        else:
            print(scad)    
