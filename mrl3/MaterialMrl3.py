# -*- coding: utf-8 -*-
"""
Created on Sun Jan 13 00:07:45 2019

@author: AsteriskAmpersand
"""

from collections import OrderedDict

from common import Cstruct as CS
from common.FileLike import FileLike
from common.crc import CrcJamcrc
from mrl3.maptype import maptypeTranslation
from mrl3.shadertype import shaderTranslation as shadermapTranslation
#from gui.materialMime import unpackMatHashes
from PyQt5 import QtCore
import re
import struct
import hashlib

def translation(maptype):
    localHash = maptype>>12
    if localHash in maptypeTranslation:
        return maptypeTranslation[localHash]
    else:
        return "Unknown Maptype"
def shaderTranslation(shaderType):
    localHash = shaderType >> 12
    return shadermapTranslation[localHash]
#shaderTranslation = lambda x: shadertypeTranslation[x>>12]
intBytes = lambda x: int.from_bytes(x, byteorder='little', signed=False)
hex_read = lambda f,x: intBytes(f.read(x))

generalhash =  lambda x:  CrcJamcrc.calc(x.encode())
padding = lambda x: b'\x00'*((16-((x)%16))%16)

class MRL3Header(CS.PyCStruct):
    fields = OrderedDict([
            ("headId","long"),
            ("unknArr","byte[12]"),
            ("materialCount","ulong"),
            ("textureCount","ulong"),
            ("textureOffset","uint64"),
            ("materialOffset","uint64")            
            ])
    def create(self):
        self.headId = 0x4C524D
        self.unknArr = [12,0,0,0, 42,102,7,93, 0,0,0,0]
        self.materialCount = 0
        self.textureCount = 0
        self.textureOffset = 0x28
        self.materialOffset = 0x28

class MRL3Texture(CS.PyCStruct):
    fields = OrderedDict([
            ("textureId","long"),
            ("unknArr","ubyte[12]"),
            ("path","char[256]")    
            ])
    
    def marshall(self,data):
        super().marshall(data)
        self.path = self.path.replace("\x00","")
    
    def create(self):
        self.textureId = 0x241F5DEB
        self.unknArr = [0]*12
        self.path = ""
        return self

    def getRole(self, role):
        if role == QtCore.Qt.DisplayRole or role == QtCore.Qt.EditRole:
            return self.path.replace("\x00","")
        
    def __str__(self):
        return self.path.replace("\x00","")
        
class MRL3ResourceBinding(CS.PyCStruct):
    resourceTypes = ["cbuffer", "sampler", "texture"]
    fields = OrderedDict([
            ("resourceType","ubyte"),#[cbuffer, sampler, texture]
            ("unknArr","ubyte[3]"),
            ("mapType","uint"),#Type of the Texture (Albedo Diffuse etc)
            ("texIdx","uint"),
            ("unkn","int"),
            ])
    
    def marshall(self, data):
        super().marshall(data)
        self.mapTypeName = translation(self.mapType)
        self.resourceTypeName = self.resourceTypes[self.resourceType&0xF]
        
    def getRole(self, role):
        if role == QtCore.Qt.DisplayRole:
            return self.resourceTypeName+": "+self.mapTypeName.replace("__disclosure","")
        
    def setIdx(self, value):
        if self.resourceType&0xF == 2:
            self.texIdx = value
        

class MRL3MaterialHeader(CS.PyCStruct):
    fields = OrderedDict([
            ("headId","uint"),
            ("materialNameHash","uint"),
            ("shaderHash","uint"),
            ("skinid","uint"),
            ("matSize","uint"),
            ("unkn4","short"),
            ("floatArrayOffset","ubyte"),
            ("unkn5","ubyte[9]"),
            ("unkn6","ubyte"),
            ("unkn7","ubyte[15]"),            
            ("startAddress","uint"),
            ("unkn8","long")])
    def __init__(self,resolver):
        self.resolver = resolver
        super().__init__()
    def setNameHash(self, newNameHash):
        if "0x" == newNameHash[0:2]:
            try:
                self.materialNameHash = int(newNameHash,base=16)
                return
            except:
                pass
        self.materialNameHash = generalhash(newNameHash)
    def getUnkn(self,i):
        obj, ix = (self.unkn7,i-9) if i>8 else (self.unkn5, i)
        return obj[ix]
    def metaSetUnkn(self, i):
        obj, ix = (self.unkn7,i-9) if i>8 else (self.unkn5, i)
        return lambda x: obj.__setitem__(ix, x)
       
class MRL3ParameterArray():
    def __init__(self, resources):
        self.Parameters = []
        self.bindingTypes = {}
        for binding in resources:
            if not (binding.resourceType & 0xF):
                binding = shaderTranslation(binding.mapType)()
                self.bindingTypes[type(binding).__name__] = len(self.Parameters)
                self.Parameters.append(binding)
    def marshall(self, data):
        for parameter in self.Parameters:
            parameter.marshall(data)
            if data.tell()%16:
                data.skip((16-(data.tell()%16))%16)
    def serialize(self):
        serializedData = b''
        for param in self.Parameters:
            serializedData+=param.serialize()
            serializedData+= padding(len(serializedData))
        return serializedData
    def __getitem__(self, ix):
        return self.Parameters[ix]
    def __iter__(self):
        return self.Parameters.__iter__()
    def __contains__(self,binding):
        return type(binding).__name__ in self.bindingTypes
    def index(self,binding):
        return self.bindingTypes[type(binding).__name__]
    def indexGet(self,binding):
        return self[self.bindingTypes[type(binding).__name__]]
    
class MRL3Material():
    def __init__(self, resolver = lambda x: hex(x), resources = None):
        self.Header = MRL3MaterialHeader(resolver)
        self.resourceBindings = []
        self.paramArray = []#MRL3ParameterArray()
        self.Resolver = resolver
        self.ResourceData = resources
    
    def marshall(self, data):
        self.Header.marshall(data)
        pos = data.tell()
        data.seek(self.Header.startAddress)
        self.resourceBindings = [MRL3ResourceBinding() for _ in range(self.Header.floatArrayOffset*8//len(MRL3ResourceBinding()))]
        [arg.marshall(data) for arg in self.resourceBindings]
        self.paramArray = MRL3ParameterArray(self.resourceBindings)#data.read(self.Header.matSize-len(self.resourceBindings)*len(MRL3ResourceBinding()))
        self.paramArray.marshall(data)
        data.seek(pos)
        
    def serialize(self):
        return self.Header.serialize(),\
                b''.join(map(lambda x: x.serialize(),self.resourceBindings)),\
                self.paramArray.serialize()
    
    def repoint(self, fromIx, toIx):
        for material in self.resourceBindings:
            idx = material.texIdx
            material.setIdx(toIx if idx==fromIx else idx)
    
    def getAlbedoIndex(self):
        for resource in self.resourceBindings:
            if "Albedo".upper() in resource.mapTypeName.upper():
                return resource.texIdx
        return 0
    
    def getRole(self, role):
        if role == QtCore.Qt.DisplayRole:
            return self.Resolver(self.Header.materialNameHash)+" - "+hex(self.Header.shaderHash)[2:]+":"+hex(self.Header.skinid)[2:]

class MRL3():
    def __init__(self, resolver = lambda x: hex(x)):
        self.Header = MRL3Header()
        self.Textures = []
        self.Materials = []
        self.Resolver = resolver
        
    def create(self):
        self.Header.create()
        
    def marshall(self, file):
        if getattr(file, "skip", False) == False: file = FileLike(file.read())
        self.Header.marshall(file)
        file.seek(self.Header.textureOffset)
        self.Textures = [MRL3Texture() for _ in range(self.Header.textureCount)]
        [mat.marshall(file) for mat in self.Textures]
        file.seek(self.Header.materialOffset)
        self.Materials = [MRL3Material(self.Resolver, self.Textures) for _ in range(self.Header.materialCount)]
        [mat.marshall(file) for mat in self.Materials]
        
    def __getitem__(self, materialString):
        idHash = generalhash(materialString)
        for material in self.Materials:
            if material.Header.materialNameHash == idHash:
                index = material.getAlbedoIndex()-1
                if index < 0 or index > len(self.Textures):
                    raise KeyError
                return self.Textures[index].path.replace("\x00","")
        raise KeyError
        
    def newTexture(self, ix = None):
        if ix is None: ix = self.Textures.rowCount()
        for material in self.Materials:
            for resource in material.resourceBindings:
                if ix < resource.texIdx < len(self.Textures)+1:
                    resource.setIdx( resource.texIdx + 1)
        self.Textures.insertRows(self.Textures.rowCount(), 1)
    def delTexture(self, ix):
        for material in self.Materials:
            for resource in material.resourceBindings:
                if ix+1 == resource.texIdx:
                    resource.setIdx( 0 )
                elif ix+1 < resource.texIdx < len(self.Textures)+1:
                    resource.setIdx( resource.texIdx - 1 )
        self.Textures.removeRows(ix, 1, QtCore.QModelIndex())
        
    def swapTex(self, ixFro, ixTo):
        for material in self.Materials:
            for resource in material.resourceBindings:
                if ixFro+1 == resource.texIdx:
                    resource.texIdx = ixTo+1 if ixFro>=ixTo else ixTo
                elif ixTo < resource.texIdx < ixFro+1:
                    resource.setIdx( resource.texIdx + 1 )
                elif ixFro+1 < resource.texIdx < ixTo+1:
                    resource.setIdx( resource.texIdx - 1 )
        
    def repointTexture(self, fro, to):
        for material in self.Materials:
            material.repoint(fro, to)    
        
    def getMaterialHashes(self):
        return [mat.Header.materialNameHash for mat in self.Materials]
        
    def updateCountsAndOffsets(self):
        self.Header.materialCount = len(self.Materials)
        self.Header.textureCount = len(self.Textures)
        self.Header.materialOffset = len(self.Header)+self.Header.textureCount*len(MRL3Texture())
        position = self.Header.materialOffset + len(self.Materials)*len(MRL3MaterialHeader(None))
        position += len(padding(position))
        for material in self.Materials:
            material.Header.startAddress = position
            position += material.Header.matSize
        
    def serialize(self):
        serialization = b''
        serialization += self.Header.serialize()
        serialization += b''.join([texture.serialize() for texture in self.Textures])
        Materials = list(zip(*[material.serialize() for material in self.Materials]))
        if not Materials:
            return serialization
        serialization += b''.join(Materials[0])
        for resource,params in zip(Materials[1],Materials[2]):
            serialization += padding(len(serialization))
            serialization += resource+params
        return serialization
    
    def coalesce(self, mrl3, row):
        currentResources = [str(tex) for tex in self.Textures]
        mapper = {0:0}
        for ex, tex in enumerate(mrl3.Textures):
            ix = ex+1
            try:
                mx = currentResources.index(str(tex))+1
            except:
                currentResources.append(str(tex))
                self.Textures.insertRow(len(self.Textures))
                self.Textures[-1] = tex
                self.Textures.setData(self.Textures.index(len(self.Textures)-1,0,QtCore.QModelIndex()),str(tex),QtCore.Qt.EditRole)
                mx = len(self.Textures)
            mapper[ix]=mx
        for material in reversed(mrl3.Materials):
            for resource in material.resourceBindings:
                resource.setIdx( mapper[resource.texIdx] if resource.texIdx in mapper else resource.texIdx )
            material.ResourceData = self.Textures
            self.Materials.insertRow(row)
            self.Materials.setData(self.Materials.index(row,0,QtCore.QModelIndex()),material,QtCore.Qt.EditRole)
            
    def generateHash(self):
        return hashlib.blake2b(self.serialize()).digest()
    
  
if "__main__" in __name__:
    from pathlib import Path
    testPath = Path(r"E:\IBProjects\ArmorPorts\Lightning - Copy\otomo\equip\ot035\body\mod\ot_body035.mrl3")#E:\MHW\Merged\Master_MtList.mrl3")
    m = MRL3()
    with open(testPath,"rb") as tFile:
        data = tFile.read()
        m.marshall(FileLike(data))
    for ix,(b,j) in enumerate(zip(data, m.serialize())):
        if b!=j:
            print (ix)
            break